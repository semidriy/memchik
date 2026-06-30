"""Re-check OP subscription whenever a user interacts with the menu.

The /start handler checks OP once. If the user later unsubscribes, /start is not
re-fired — they just keep navigating the menu. This middleware closes that gap:
for any user-facing menu callback, verify subscription before letting the handler run.

Skipped for admin callbacks and for the OP-check callback itself (to avoid loops).
"""
import logging
from typing import Callable, Any, Awaitable

import aiohttp
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery

from bot.database import get_pool
from bot.database.queries import get_active_op_channels, get_bot_message, check_join_request
from bot.keyboards.user import op_check_keyboard
from bot.services.permissions import is_super_admin
from bot.services.i18n import user_lang

logger = logging.getLogger(__name__)

# ОП-гейт перенесён со /start на действия создания/использования (просьба клиента:
# «оп из старта в новый шаблон перенести»). Подписку проверяем ТОЛЬКО когда юзер
# жмёт «новый шаблон» или «создать мем» — просмотр меню больше не блокируется.
# Это же закрывает «если отписался — просить подписаться снова»: на каждом таком
# клике подписка перепроверяется заново.
_GATED_CALLBACKS = frozenset({
    "template:add_user",  # «➕ Новый шаблон»
    "meme:start",         # «🎭 Создать мем»
})


async def _bot_started(token: str, user_id: int) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"https://api.telegram.org/bot{token}/sendChatAction",
                json={"chat_id": user_id, "action": "typing"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json()
                return data.get("ok", False)
    except Exception:
        return True


async def _is_subscribed(bot, pool, user_id: int, channels: list[dict]) -> bool:
    has_checkable = False
    for ch in channels:
        ch_type = ch.get("channel_type", "channel")
        if ch_type in ("link", "folder"):
            continue
        if ch_type == "bot":
            token = ch.get("bot_token")
            if not token:
                continue
            has_checkable = True
            if not await _bot_started(token, user_id):
                return False
            continue
        cid = ch.get("channel_id")
        if not cid:
            continue
        has_checkable = True
        # Закрытый канал (режим «заявки»): членство ещё не видно, пока заявку не
        # одобрят, поэтому сначала засчитываем поданную заявку как пройденную ОП.
        if await check_join_request(pool, user_id, cid):
            continue
        try:
            member = await bot.get_chat_member(cid, user_id)
            if member.status in ("left", "kicked", "restricted"):
                return False
        except Exception:
            return False
    return has_checkable


class OpRecheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery) or not event.data:
            return await handler(event, data)

        if event.data not in _GATED_CALLBACKS:
            return await handler(event, data)

        user_id = event.from_user.id
        if is_super_admin(user_id):
            return await handler(event, data)

        try:
            pool = get_pool()
            channels = await get_active_op_channels(pool)
            if not channels:
                return await handler(event, data)
            if await _is_subscribed(event.bot, pool, user_id, channels):
                return await handler(event, data)
            # User no longer subscribed → show OP screen, stop handler
            lang = await user_lang(user_id)
            op_msg = await get_bot_message(pool, "op_required", lang)
            text = op_msg["text"] or "Для доступа подпишись на наши каналы:"
            kb = op_check_keyboard(channels, None)
            ents = None
            if op_msg.get("entities"):
                from aiogram.types import MessageEntity
                ents = [MessageEntity(**{k: v for k, v in e.items()}) for e in op_msg["entities"]]
            try:
                if ents:
                    await event.message.edit_text(text, entities=ents, parse_mode=None, reply_markup=kb)
                else:
                    await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                # Текущее сообщение может быть медиа-менюшкой → edit_text по нему нельзя,
                # пересоздаём текстовым сообщением.
                logger.debug("op-recheck edit failed, resending: %s", e)
                try:
                    await event.message.delete()
                except Exception:
                    pass
                try:
                    if ents:
                        await event.message.answer(text, entities=ents, parse_mode=None, reply_markup=kb)
                    else:
                        await event.message.answer(text, parse_mode="HTML", reply_markup=kb)
                except Exception as e2:
                    logger.debug("op-recheck resend failed: %s", e2)
            await event.answer()
            return None
        except Exception as e:
            logger.warning("OpRecheckMiddleware error: %s", e)
            return await handler(event, data)
