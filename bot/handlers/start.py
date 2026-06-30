import logging
import aiohttp

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, ChatJoinRequest, MessageEntity,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_ad_link_by_code, record_visit, mark_op_passed, mark_op_passed_by_code,
    get_active_op_channels, get_template, get_active_displays, record_display_view,
    get_bot_message, get_bot_buttons, pin_template_for_user, is_user_bot_premium,
    log_join_request, check_join_request,
)
from bot.keyboards.user import main_menu, build_inline_kb
from bot.services.detector import is_suspicious_user
from bot.services.i18n import t, user_lang
from bot.states.admin import MemeStates

router = Router()
logger = logging.getLogger(__name__)


async def _check_bot_started(token: str, user_id: int) -> bool:
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


async def _send_bot_message(message: Message, msg: dict, kb, **fmt):
    """Отправить bot_message свежим сообщением. Если у менюшки задано медиа
    (видео/гиф/фото) — шлём как медиа с подписью=text, иначе обычным текстом.
    format(**fmt) применяем только когда нет entities (иначе сместятся офсеты)."""
    text = msg.get("text") or ""
    entities = None
    if msg.get("entities"):
        entities = [MessageEntity(**{k: v for k, v in e.items()}) for e in msg["entities"]]
    elif fmt:
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError):
            pass

    file_id = msg.get("file_id")
    file_type = msg.get("file_type")
    if file_id:
        kw = {"caption": text or None, "reply_markup": kb}
        if entities:
            kw["caption_entities"] = entities
        else:
            kw["parse_mode"] = "HTML"
        if file_type == "video":
            await message.answer_video(file_id, **kw)
        elif file_type == "photo":
            await message.answer_photo(file_id, **kw)
        else:
            await message.answer_animation(file_id, **kw)
        return
    if entities:
        await message.answer(text or "Привет!", entities=entities, parse_mode=None, reply_markup=kb)
    else:
        await message.answer(text or "Привет!", parse_mode="HTML", reply_markup=kb)


@router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest):
    """Юзер подал заявку в закрытый ОП-канал — фиксируем её, чтобы засчитать подписку
    (в приватном канале членство через get_chat_member не видно, пока заявка висит)."""
    try:
        await log_join_request(get_pool(), event.from_user.id, event.chat.id)
    except Exception as e:
        logger.warning("log_join_request failed: %s", e)


async def _send_displays(bot, chat_id: int, user_id: int, pool):
    try:
        displays = await get_active_displays(pool)
        for d in displays:
            kb = None
            if d.get("button_text") and d.get("button_url"):
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=d["button_text"], url=d["button_url"])
                ]])
            caption = d.get("caption_text") or None
            file_id = d.get("file_id")
            file_type = d.get("file_type")
            try:
                if file_id and file_type == "animation":
                    await bot.send_animation(chat_id, file_id, caption=caption, reply_markup=kb)
                elif file_id and file_type == "photo":
                    await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=kb)
                elif caption:
                    await bot.send_message(chat_id, caption, reply_markup=kb)
                else:
                    continue
                await record_display_view(pool, user_id, d["id"])
            except Exception:
                continue
    except Exception:
        pass


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    pool = get_pool()

    lang = await user_lang(message.from_user.id)

    if args.startswith("gif_"):
        try:
            template_id = int(args[4:])
        except ValueError:
            await message.answer(await t("start.bad_link", lang))
            return
        pool = get_pool()
        tpl = await get_template(pool, template_id)
        if not tpl or not tpl["is_active"]:
            await message.answer(await t("meme.tpl_not_found", lang))
            return
        await state.set_state(MemeStates.waiting_text)
        await state.update_data(template_id=template_id, file_id=tpl["file_id"], file_type=tpl["file_type"])
        if tpl["file_type"] == "animation":
            await message.answer_animation(tpl["file_id"])
        else:
            await message.answer_photo(tpl["file_id"])
        await message.answer(await t("meme.send_text_short", lang))
        return

    if args.startswith("tpl_"):
        try:
            template_id = int(args[4:])
        except ValueError:
            await message.answer(await t("tpl.pin_invalid", lang))
            return
        tpl = await pin_template_for_user(pool, message.from_user.id, template_id)
        bot_info = await message.bot.get_me()
        if not tpl:
            await message.answer(await t("tpl.pin_not_found", lang))
        else:
            title = tpl.get("title") or await t("tpl.no_title", lang)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.send_to_friend", lang), switch_inline_query="")],
                [InlineKeyboardButton(text=await t("common.add_more", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="menu:open")],
            ])
            await message.answer(
                await t("tpl.pin_added", lang, title=title, bot_username=bot_info.username),
                parse_mode="HTML",
                reply_markup=kb,
            )
        return

    link_code = args[4:] if args.startswith("ref_") else None

    if link_code:
        link = await get_ad_link_by_code(pool, link_code)
        if link:
            prev_visits = await pool.fetchval(
                "SELECT COUNT(*) FROM link_visits WHERE link_id = $1 AND user_id = $2",
                link["id"], message.from_user.id,
            )
            suspected = is_suspicious_user(message.from_user, prev_visits)
            await record_visit(pool, link["id"], message.from_user.id, suspected)

    # ОП-гейт перенесён со /start на действия создания («новый шаблон» / «создать мем»):
    # на старте больше НЕ блокируем — пользователь сразу видит меню. Подписка
    # проверяется в момент этих действий (см. OpRecheckMiddleware). Это же закрывает
    # пункт «если отписался — просить подписаться снова»: каждый такой клик
    # перепроверяет подписку.
    if link_code:
        await mark_op_passed_by_code(pool, link_code, message.from_user.id)

    bot_info = await message.bot.get_me()
    msg = await get_bot_message(pool, "welcome", lang)
    btn_rows = await get_bot_buttons(pool, "main_menu", lang)
    kb = build_inline_kb(btn_rows) if btn_rows else main_menu()
    await _send_bot_message(message, msg, kb,
                            name=message.from_user.first_name, bot_username=bot_info.username)

    if not await is_user_bot_premium(pool, message.from_user.id):
        await _send_displays(message.bot, message.chat.id, message.from_user.id, pool)


@router.callback_query(F.data.startswith("op:check"))
async def cb_op_check(callback: CallbackQuery):
    pool = get_pool()
    parts = callback.data.split(":")
    link_code = parts[2] if len(parts) > 2 else None
    lang = await user_lang(callback.from_user.id)

    try:
        channels = await get_active_op_channels(pool)
        if not channels:
            await callback.message.edit_text(await t("common.menu", lang), reply_markup=main_menu())
            await callback.answer()
            return

        not_subscribed = []
        for ch in channels:
            ch_type = ch.get("channel_type", "channel")
            if ch_type in ("link", "folder"):
                continue
            if ch_type == "bot":
                token = ch.get("bot_token")
                if not token:
                    continue
                if not await _check_bot_started(token, callback.from_user.id):
                    not_subscribed.append(ch["title"])
                continue
            cid = ch.get("channel_id")
            if not cid:
                continue
            # Закрытый канал (режим «заявки»): пока заявку не одобрили, get_chat_member
            # отдаёт 'left', поэтому сначала проверяем сам факт поданной заявки.
            if await check_join_request(pool, callback.from_user.id, cid):
                continue
            try:
                member = await callback.bot.get_chat_member(cid, callback.from_user.id)
                if member.status in ("left", "kicked", "restricted"):
                    not_subscribed.append(ch["title"])
            except Exception as e:
                logger.warning("get_chat_member failed for channel %s: %s", cid, e)
                not_subscribed.append(ch["title"])

        if not_subscribed:
            await callback.answer(
                await t("op.not_subscribed", lang, channels=", ".join(not_subscribed)),
                show_alert=True,
            )
            return

        if link_code:
            await mark_op_passed_by_code(pool, link_code, callback.from_user.id)

        msg = await get_bot_message(pool, "welcome", lang)
        btn_rows = await get_bot_buttons(pool, "main_menu", lang)
        kb2 = build_inline_kb(btn_rows) if btn_rows else main_menu()
        raw_text = msg["text"] or "Привет!"

        if msg.get("file_id"):
            # У welcome задано медиа — текстовый ОП-экран нельзя «доредактировать»
            # в медиа, поэтому удаляем его и шлём свежее медиа-сообщение.
            try:
                await callback.message.delete()
            except Exception:
                pass
            await _send_bot_message(callback.message, msg, kb2, name=callback.from_user.first_name)
        elif msg["entities"]:
            ents = [MessageEntity(**{k: v for k, v in e.items()}) for e in msg["entities"]]
            await callback.message.edit_text(raw_text, entities=ents, parse_mode=None, reply_markup=kb2)
        else:
            try:
                text = raw_text.format(name=callback.from_user.first_name)
            except (KeyError, IndexError):
                text = raw_text
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb2)

        await callback.answer()
        if not await is_user_bot_premium(pool, callback.from_user.id):
            await _send_displays(callback.bot, callback.message.chat.id, callback.from_user.id, pool)

    except Exception as e:
        logger.exception("cb_op_check error: %s", e)
        try:
            await callback.answer(await t("op.error", lang), show_alert=True)
        except Exception:
            pass
