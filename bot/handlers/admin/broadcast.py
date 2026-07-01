import asyncio
import json as _json
import logging
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaAudio,
    InputMediaDocument, InputMediaAnimation,
    MessageEntity,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    create_broadcast, update_broadcast_status,
    get_broadcast, get_recent_broadcasts, get_all_user_ids,
    mark_user_blocked,
)
from bot.keyboards.admin import (
    broadcast_menu, broadcast_preview, broadcast_running, broadcast_stopped,
)
from bot.states.admin import BroadcastStates

router = Router()
_running_broadcasts: dict[int, bool] = {}

# Album debounce buffer: media_group_id -> list of messages
_album_buffer: dict[str, list[Message]] = {}
_album_tasks: dict[str, asyncio.Task] = {}

_CONTENT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel_input")],
])


from bot.services.permissions import has_permission, PERM_BROADCAST


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_BROADCAST)


async def _edit_msg(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
        except TelegramBadRequest:
            pass


async def _bot_edit_msg(bot, chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
    except TelegramBadRequest:
        try:
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, reply_markup=reply_markup)
        except TelegramBadRequest:
            pass


def _serialize_entities(entities) -> list:
    if not entities:
        return []
    try:
        return [e.model_dump(mode='json', exclude_none=True) for e in entities]
    except Exception:
        return []


def _serialize_markup(markup) -> dict | None:
    if not markup or not isinstance(markup, InlineKeyboardMarkup):
        return None
    try:
        return markup.model_dump(mode='json', exclude_none=True)
    except Exception:
        return None


def _get_forward_source(message: Message) -> tuple[int | None, int | None]:
    """Returns (source_chat_id, source_message_id) for forwarded messages."""
    # aiogram 3.x new API
    if message.forward_origin:
        origin = message.forward_origin
        if hasattr(origin, 'chat') and hasattr(origin, 'message_id'):
            return origin.chat.id, origin.message_id
    # Legacy API fallback
    if message.forward_from_chat and message.forward_from_message_id:
        return message.forward_from_chat.id, message.forward_from_message_id
    return None, None


def _build_message_item(message: Message) -> dict:
    item: dict = {}
    if message.photo:
        item["type"] = "photo"
        item["file_id"] = message.photo[-1].file_id
    elif message.video:
        item["type"] = "video"
        item["file_id"] = message.video.file_id
    elif message.animation:
        item["type"] = "animation"
        item["file_id"] = message.animation.file_id
    elif message.audio:
        item["type"] = "audio"
        item["file_id"] = message.audio.file_id
    elif message.voice:
        item["type"] = "voice"
        item["file_id"] = message.voice.file_id
    elif message.video_note:
        item["type"] = "video_note"
        item["file_id"] = message.video_note.file_id
    elif message.document:
        item["type"] = "document"
        item["file_id"] = message.document.file_id
    elif message.sticker:
        item["type"] = "sticker"
        item["file_id"] = message.sticker.file_id
    else:
        item["type"] = "text"

    if message.text:
        item["text"] = message.text
        ents = _serialize_entities(message.entities)
        if ents:
            item["entities"] = ents

    if message.caption:
        item["caption"] = message.caption
        ents = _serialize_entities(message.caption_entities)
        if ents:
            item["caption_entities"] = ents

    if getattr(message, 'has_media_spoiler', False):
        item["has_media_spoiler"] = True

    return item


def _make_keyboard(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = []
    for row in buttons:
        rows.append([InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row if "url" in b])
    rows = [r for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _parse_buttons(text: str) -> list[list[dict]]:
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split(","):
            part = part.strip()
            if " - " in part:
                btn_text, url = part.rsplit(" - ", 1)
            elif "|" in part:
                btn_text, url = part.rsplit("|", 1)
            else:
                continue
            btn_text, url = btn_text.strip(), url.strip()
            if btn_text and url:
                row.append({"text": btn_text, "url": url})
        if row:
            rows.append(row)
    return rows


def _build_reply_markup(broadcast: dict) -> InlineKeyboardMarkup | None:
    """Build reply_markup: admin buttons override original, else use original."""
    admin_buttons = broadcast.get("buttons") or []
    if admin_buttons:
        return _make_keyboard(admin_buttons)
    # fallback: original markup from forwarded post
    msg_data = broadcast.get("message_data") or {}
    original = msg_data.get("original_markup")
    if original:
        try:
            return InlineKeyboardMarkup.model_validate(original)
        except Exception:
            pass
    return None


@router.callback_query(F.data == "broadcast:new")
async def cb_broadcast_new(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastStates.waiting_content)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "Перешли пост из канала или отправь любое сообщение для рассылки.\n\n"
        "Поддерживается: текст, фото, видео, GIF, голосовые, файлы, стикеры, альбомы.",
        reply_markup=_CONTENT_KB,
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast:cancel_input")
async def cb_broadcast_cancel_input(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("Рассылка", reply_markup=broadcast_menu())
    await callback.answer()


@router.message(BroadcastStates.waiting_content)
async def handle_broadcast_content(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    # Album (media group) handling
    if message.media_group_id:
        group_id = message.media_group_id
        if group_id not in _album_buffer:
            _album_buffer[group_id] = []
        _album_buffer[group_id].append(message)

        # Cancel previous task and reschedule
        if group_id in _album_tasks:
            _album_tasks[group_id].cancel()

        data = await state.get_data()
        _album_tasks[group_id] = asyncio.create_task(
            _finalize_album(group_id, message, state, data)
        )
        return

    # Single message
    data = await state.get_data()
    await _create_broadcast_from_message(message, state, data, [message])


async def _finalize_album(group_id: str, last_message: Message, state: FSMContext, data: dict):
    await asyncio.sleep(0.8)
    messages = _album_buffer.pop(group_id, [])
    _album_tasks.pop(group_id, None)
    if not messages:
        return
    messages.sort(key=lambda m: m.message_id)
    await _create_broadcast_from_message(last_message, state, data, messages)


async def _create_broadcast_from_message(
    trigger_message: Message,
    state: FSMContext,
    data: dict,
    messages: list[Message],
):
    pool = get_pool()
    is_album = len(messages) > 1

    # Always store source for copy_message (admin private chat with bot)
    source_chat_id = messages[0].chat.id
    source_message_ids = [m.message_id for m in messages]

    # Check if forwarded from channel — prefer original source
    forward_chat_id, _ = _get_forward_source(messages[0])
    forward_message_ids = []
    if forward_chat_id:
        for msg in messages:
            _, mid = _get_forward_source(msg)
            if mid:
                forward_message_ids.append(mid)

    # Build items as fallback
    items = [_build_message_item(m) for m in messages]

    original_markup = _serialize_markup(messages[0].reply_markup)

    message_data = {
        "items": items,
        "source_chat_id": source_chat_id,
        "source_message_ids": source_message_ids,
    }
    if forward_chat_id and forward_message_ids:
        message_data["forward_chat_id"] = forward_chat_id
        message_data["forward_message_ids"] = forward_message_ids
    if original_markup:
        message_data["original_markup"] = original_markup

    # Legacy fields for backward compatibility
    first = messages[0]
    if first.photo:
        ct, fid, txt = "photo", first.photo[-1].file_id, first.caption
    elif first.video:
        ct, fid, txt = "video", first.video.file_id, first.caption
    elif first.animation:
        ct, fid, txt = "animation", first.animation.file_id, first.caption
    elif first.audio:
        ct, fid, txt = "audio", first.audio.file_id, first.caption
    elif first.voice:
        ct, fid, txt = "voice", first.voice.file_id, first.caption
    elif first.video_note:
        ct, fid, txt = "video_note", first.video_note.file_id, None
    elif first.document:
        ct, fid, txt = "document", first.document.file_id, first.caption
    elif first.sticker:
        ct, fid, txt = "sticker", first.sticker.file_id, None
    elif is_album:
        ct, fid, txt = "album", None, None
    else:
        ct, fid, txt = "text", None, first.html_text

    broadcast = await create_broadcast(pool, ct, txt, fid, message_data=message_data)
    await state.update_data(broadcast_id=broadcast["id"])
    await state.set_state(BroadcastStates.confirm)

    try:
        await _bot_edit_msg(
            trigger_message.bot, data["pm_cid"], data["pm_mid"],
            "Предпросмотр рассылки ниже",
        )
    except Exception:
        pass

    await _send_broadcast_preview(trigger_message, broadcast)


@router.message(BroadcastStates.editing_content)
async def handle_broadcast_edit(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    data = await state.get_data()
    broadcast_id = data.get("broadcast_id")
    if not broadcast_id:
        await state.clear()
        return

    # Re-use same creation logic but update existing broadcast
    pool = get_pool()
    old = await get_broadcast(pool, broadcast_id)
    if not old:
        await state.clear()
        return

    # Handle album here too
    if message.media_group_id:
        group_id = message.media_group_id
        if group_id not in _album_buffer:
            _album_buffer[group_id] = []
        _album_buffer[group_id].append(message)
        if group_id in _album_tasks:
            _album_tasks[group_id].cancel()
        _album_tasks[group_id] = asyncio.create_task(
            _finalize_album_edit(group_id, message, state, data, broadcast_id)
        )
        return

    await _do_edit_broadcast(message, state, data, broadcast_id, [message], pool)


async def _finalize_album_edit(group_id, last_message, state, data, broadcast_id):
    await asyncio.sleep(0.8)
    messages = _album_buffer.pop(group_id, [])
    _album_tasks.pop(group_id, None)
    if not messages:
        return
    messages.sort(key=lambda m: m.message_id)
    pool = get_pool()
    await _do_edit_broadcast(last_message, state, data, broadcast_id, messages, pool)


async def _do_edit_broadcast(trigger_message, state, data, broadcast_id, messages, pool):
    first = messages[0]
    is_album = len(messages) > 1

    source_chat_id = first.chat.id
    source_message_ids = [m.message_id for m in messages]

    forward_chat_id, _ = _get_forward_source(first)
    forward_message_ids = []
    if forward_chat_id:
        for msg in messages:
            _, mid = _get_forward_source(msg)
            if mid:
                forward_message_ids.append(mid)

    items = [_build_message_item(m) for m in messages]
    original_markup = _serialize_markup(first.reply_markup)

    message_data = {
        "items": items,
        "source_chat_id": source_chat_id,
        "source_message_ids": source_message_ids,
    }
    if forward_chat_id and forward_message_ids:
        message_data["forward_chat_id"] = forward_chat_id
        message_data["forward_message_ids"] = forward_message_ids
    if original_markup:
        message_data["original_markup"] = original_markup

    if first.photo:
        ct, fid, txt = "photo", first.photo[-1].file_id, first.caption
    elif first.video:
        ct, fid, txt = "video", first.video.file_id, first.caption
    elif first.animation:
        ct, fid, txt = "animation", first.animation.file_id, first.caption
    elif first.audio:
        ct, fid, txt = "audio", first.audio.file_id, first.caption
    elif first.voice:
        ct, fid, txt = "voice", first.voice.file_id, first.caption
    elif first.video_note:
        ct, fid, txt = "video_note", first.video_note.file_id, None
    elif first.document:
        ct, fid, txt = "document", first.document.file_id, first.caption
    elif first.sticker:
        ct, fid, txt = "sticker", first.sticker.file_id, None
    elif is_album:
        ct, fid, txt = "album", None, None
    else:
        ct, fid, txt = "text", None, first.html_text

    await pool.execute(
        "UPDATE broadcasts SET content_type=$1, text=$2, file_id=$3, message_data=$4::jsonb WHERE id=$5",
        ct, txt, fid, _json.dumps(message_data), broadcast_id,
    )
    broadcast = await get_broadcast(pool, broadcast_id)
    await state.set_state(BroadcastStates.confirm)

    try:
        await _bot_edit_msg(
            trigger_message.bot, data["pm_cid"], data["pm_mid"],
            "Обновлённый предпросмотр ниже",
        )
    except Exception:
        pass

    await _send_broadcast_preview(trigger_message, broadcast)


async def _send_broadcast_preview(message: Message, broadcast: dict):
    bid = broadcast["id"]
    ct = broadcast["content_type"]
    total = broadcast["total_users"]
    has_buttons = bool(broadcast.get("buttons"))
    msg_data = broadcast.get("message_data") or {}
    markup = broadcast_preview(bid, has_buttons)

    warn = " ⚠️ все заблокированы!" if total == 0 else ""
    info_text = f"#{bid} · {ct} · {total} получателей{warn}"

    source_chat_id = msg_data.get("source_chat_id")
    source_message_ids = msg_data.get("source_message_ids") or []

    copy_chat = source_chat_id
    copy_ids = source_message_ids
    user_markup = _build_reply_markup(broadcast)

    try:
        if copy_chat and copy_ids:
            ids = sorted(copy_ids)
            if len(ids) == 1:
                await message.bot.copy_message(
                    message.chat.id, from_chat_id=copy_chat, message_id=ids[0],
                    reply_markup=user_markup,
                )
            else:
                await message.bot.copy_messages(message.chat.id, from_chat_id=copy_chat, message_ids=ids)
            await message.answer(info_text, reply_markup=markup)
            return

        # Fallback: legacy broadcasts without source
        items = msg_data.get("items") or []
        if items:
            first = items[0]
            fid = first.get("file_id")
            cap = first.get("caption") or first.get("text") or ""
            t = first.get("type", "text")
            if t == "photo":
                await message.answer_photo(fid, caption=cap, parse_mode=None, reply_markup=user_markup)
            elif t == "video":
                await message.answer_video(fid, caption=cap, parse_mode=None, reply_markup=user_markup)
            elif t == "animation":
                await message.answer_animation(fid, caption=cap, parse_mode=None, reply_markup=user_markup)
            elif t == "document":
                await message.answer_document(fid, caption=cap, parse_mode=None, reply_markup=user_markup)
            else:
                await message.answer(cap, parse_mode=None, reply_markup=user_markup)
            await message.answer(info_text, reply_markup=markup)
        else:
            text = broadcast.get("text") or ""
            file_id = broadcast.get("file_id")
            if ct == "photo":
                await message.answer_photo(file_id, caption=text + f"\n\n{info_text}", parse_mode=None, reply_markup=markup)
            elif ct == "video":
                await message.answer_video(file_id, caption=text + f"\n\n{info_text}", parse_mode=None, reply_markup=markup)
            elif ct == "animation":
                await message.answer_animation(file_id, caption=text + f"\n\n{info_text}", parse_mode=None, reply_markup=markup)
            else:
                await message.answer((text or "(пустой текст)") + f"\n\n{info_text}", parse_mode=None, reply_markup=markup)
    except Exception as e:
        logger.warning("preview failed for broadcast #%s: %s", bid, e)
        await message.answer(
            f"Предпросмотр #{bid} · {ct} · {total} получателей{warn}\n\n"
            f"⚠️ Превью: <code>{str(e)[:200]}</code>",
            parse_mode="HTML",
            reply_markup=markup,
        )


@router.callback_query(F.data.startswith("broadcast:buttons:"))
async def cb_broadcast_buttons(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    broadcast_id = int(callback.data.split(":")[2])
    await state.set_state(BroadcastStates.waiting_buttons)
    await state.update_data(
        broadcast_id=broadcast_id,
        pm_cid=callback.message.chat.id,
        pm_mid=callback.message.message_id,
    )
    await _edit_msg(
        callback.message,
        "Введи кнопки для рассылки.\n\n"
        "Формат — каждая строка = одна кнопка:\n"
        "<code>Текст кнопки - https://ссылка.com</code>\n\n"
        "Несколько в одном ряду — через запятую:\n"
        "<code>Кнопка 1 | https://url1.com, Кнопка 2 | https://url2.com</code>\n\n"
        "Убрать все кнопки — отправь <code>-</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"broadcast:cancel_buttons:{broadcast_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("broadcast:cancel_buttons:"))
async def cb_broadcast_cancel_buttons(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await _edit_msg(callback.message, "Рассылка", reply_markup=broadcast_menu())
    await callback.answer()


@router.message(BroadcastStates.waiting_buttons)
async def handle_broadcast_buttons(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    broadcast_id = data.get("broadcast_id")
    text = (message.text or "").strip()

    buttons = [] if text == "-" else _parse_buttons(text)

    pool = get_pool()
    await pool.execute(
        "UPDATE broadcasts SET buttons = $1::jsonb WHERE id = $2",
        _json.dumps(buttons), broadcast_id,
    )
    broadcast = await get_broadcast(pool, broadcast_id)
    await state.set_state(BroadcastStates.confirm)

    try:
        await message.delete()
    except Exception:
        pass

    await _bot_edit_msg(
        message.bot, data["pm_cid"], data["pm_mid"],
        "Кнопки сохранены. Предпросмотр ниже.",
    )

    await _send_broadcast_preview(message, broadcast)


@router.callback_query(F.data.startswith("broadcast:edit:"))
async def cb_broadcast_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    broadcast_id = int(callback.data.split(":")[2])
    await state.set_state(BroadcastStates.editing_content)
    await state.update_data(broadcast_id=broadcast_id, pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await _edit_msg(
        callback.message,
        "Перешли обновлённый пост или отправь новое содержимое:",
        reply_markup=_CONTENT_KB,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("broadcast:run:"))
async def cb_broadcast_run(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return

    broadcast_id = int(callback.data.split(":")[2])
    pool = get_pool()
    broadcast = await get_broadcast(pool, broadcast_id)
    if not broadcast:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return

    await state.clear()
    await update_broadcast_status(pool, broadcast_id, "running")
    _running_broadcasts[broadcast_id] = True

    await callback.message.edit_reply_markup(reply_markup=broadcast_running(broadcast_id))
    await callback.answer("Рассылка запущена!")

    asyncio.create_task(_run_broadcast(callback.bot, broadcast_id, callback.from_user.id))


@router.callback_query(F.data.startswith("broadcast:stop:"))
async def cb_broadcast_stop(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    broadcast_id = int(callback.data.split(":")[2])
    _running_broadcasts[broadcast_id] = False
    await callback.answer("Остановка рассылки...", show_alert=True)

    pool = get_pool()
    broadcast = await get_broadcast(pool, broadcast_id)
    sent = broadcast["sent_count"] if broadcast else 0
    failed = broadcast["failed_count"] if broadcast else 0

    await _edit_msg(
        callback.message,
        f"Рассылка #{broadcast_id} остановлена\nОтправлено: {sent}\nОшибок: {failed}",
        reply_markup=broadcast_stopped(),
    )


@router.callback_query(F.data.startswith("broadcast:cancel:"))
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    broadcast_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await update_broadcast_status(pool, broadcast_id, "cancelled")
    _running_broadcasts.pop(broadcast_id, None)
    await state.clear()
    await _edit_msg(callback.message, "Рассылка отменена")
    await callback.answer()


@router.callback_query(F.data.startswith("broadcast:progress:"))
async def cb_broadcast_progress(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    broadcast_id = int(callback.data.split(":")[2])
    pool = get_pool()
    broadcast = await get_broadcast(pool, broadcast_id)
    if not broadcast:
        await callback.answer("Не найдено", show_alert=True)
        return

    total = broadcast["total_users"] or 1
    sent = broadcast["sent_count"]
    failed = broadcast["failed_count"]
    pct = round((sent + failed) / total * 100)
    status_map = {"running": "В процессе", "done": "Завершено", "cancelled": "Остановлено"}
    status = status_map.get(broadcast["status"], broadcast["status"])

    text = (
        f"Рассылка #{broadcast_id}\n"
        f"Статус: {status}\n"
        f"Прогресс: {pct}% ({sent + failed}/{total})\n"
        f"Отправлено: {sent}\nОшибок: {failed}"
    )
    markup = broadcast_running(broadcast_id) if broadcast["status"] == "running" else broadcast_stopped()
    await _edit_msg(callback.message, text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "broadcast:history")
async def cb_broadcast_history(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    pool = get_pool()
    broadcasts = await get_recent_broadcasts(pool, 10)
    if not broadcasts:
        await _edit_msg(callback.message, "Рассылок ещё не было", reply_markup=broadcast_menu())
        await callback.answer()
        return

    lines = ["Последние рассылки:\n"]
    for b in broadcasts:
        icons = {"running": "🟡", "done": "✅", "cancelled": "❌", "draft": "📝"}
        icon = icons.get(b["status"], "❓")
        lines.append(f"{icon} #{b['id']} — {b['content_type']} ({b['sent_count']}/{b['total_users']})")

    await _edit_msg(callback.message, "\n".join(lines), reply_markup=broadcast_menu())
    await callback.answer()


async def _run_broadcast(bot: Bot, broadcast_id: int, admin_id: int):
    pool = get_pool()
    broadcast = await get_broadcast(pool, broadcast_id)
    user_ids = await get_all_user_ids(pool)
    sent = failed = 0
    first_error: str | None = None

    logger.info("broadcast #%s starting, users in db: %d", broadcast_id, len(user_ids))

    if not user_ids:
        _running_broadcasts.pop(broadcast_id, None)
        await update_broadcast_status(pool, broadcast_id, "done", 0, 0)
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ Рассылка #{broadcast_id}: в базе нет пользователей.\n"
                f"Убедись что кто-то запускал /start в боте.",
            )
        except Exception:
            pass
        return

    for user_id in user_ids:
        if not _running_broadcasts.get(broadcast_id):
            break
        try:
            await _send_to_user(bot, user_id, broadcast)
            sent += 1
        except TelegramForbiddenError:
            await mark_user_blocked(pool, user_id)
            failed += 1
        except Exception as e:
            if first_error is None:
                first_error = str(e)
            logger.warning("broadcast #%s send failed for %s: %s", broadcast_id, user_id, e)
            failed += 1

        if (sent + failed) % 50 == 0:
            await update_broadcast_status(pool, broadcast_id, "progress", sent, failed)

        await asyncio.sleep(0.05)

    was_running = _running_broadcasts.pop(broadcast_id, None)
    final_status = "done" if was_running else "cancelled"
    await update_broadcast_status(pool, broadcast_id, final_status, sent, failed)

    try:
        status_text = "завершена" if final_status == "done" else "остановлена"
        lines = [
            f"Рассылка #{broadcast_id} {status_text}",
            f"Отправлено: {sent}",
            f"Ошибок: {failed}",
        ]
        if first_error:
            lines.append(f"\n⚠️ Первая ошибка:\n<code>{first_error[:300]}</code>")
        await bot.send_message(admin_id, "\n".join(lines), parse_mode="HTML")
    except Exception:
        pass


async def _send_to_user(bot: Bot, user_id: int, broadcast: dict):
    msg_data = broadcast.get("message_data") or {}
    markup = _build_reply_markup(broadcast)
    items = msg_data.get("items") or []

    source_chat_id = msg_data.get("source_chat_id")
    source_message_ids = msg_data.get("source_message_ids") or []

    copy_chat = source_chat_id
    copy_ids = source_message_ids

    if copy_chat and copy_ids:
        try:
            ids = sorted(copy_ids)
            if len(ids) == 1:
                await bot.copy_message(user_id, from_chat_id=copy_chat, message_id=ids[0], reply_markup=markup)
            else:
                await bot.copy_messages(user_id, from_chat_id=copy_chat, message_ids=ids)
            return
        except Exception as e:
            logger.warning("copy_message failed for user %s, fallback to items: %s", user_id, e)

    def _ents(raw: list) -> list[MessageEntity] | None:
        if not raw:
            return None
        try:
            return [MessageEntity.model_validate(e) for e in raw]
        except Exception as exc:
            logger.warning("entity parse error: %s", exc)
            return None

    # Direct send from stored items
    if items:
        if len(items) > 1:
            media = []
            for item in items:
                cap = item.get("caption")
                cap_entities = _ents(item.get("caption_entities") or [])
                t = item["type"]
                fid = item["file_id"]
                # parse_mode=None: дефолт бота — HTML, иначе caption_entities игнорируются
                # (пропадают формат и прем-эмодзи в подписи).
                if t == "photo":
                    media.append(InputMediaPhoto(media=fid, caption=cap, caption_entities=cap_entities, parse_mode=None))
                elif t == "video":
                    media.append(InputMediaVideo(media=fid, caption=cap, caption_entities=cap_entities, parse_mode=None))
                elif t == "audio":
                    media.append(InputMediaAudio(media=fid, caption=cap, caption_entities=cap_entities, parse_mode=None))
                elif t == "document":
                    media.append(InputMediaDocument(media=fid, caption=cap, caption_entities=cap_entities, parse_mode=None))
            if media:
                await bot.send_media_group(user_id, media)
            return

        item = items[0]
        item_type = item.get("type", "text")
        file_id = item.get("file_id")
        caption = item.get("caption")
        cap_entities = _ents(item.get("caption_entities") or [])
        text = item.get("text", "")
        txt_entities = _ents(item.get("entities") or [])

        # parse_mode=None везде, где передаём entities/caption_entities: дефолт бота —
        # HTML, и без сброса Телеграм игнорирует энтити (формат и прем-эмодзи теряются).
        if item_type == "text":
            await bot.send_message(user_id, text, entities=txt_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "photo":
            await bot.send_photo(user_id, file_id, caption=caption, caption_entities=cap_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "video":
            await bot.send_video(user_id, file_id, caption=caption, caption_entities=cap_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "animation":
            await bot.send_animation(user_id, file_id, caption=caption, caption_entities=cap_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "audio":
            await bot.send_audio(user_id, file_id, caption=caption, caption_entities=cap_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "voice":
            await bot.send_voice(user_id, file_id, caption=caption, caption_entities=cap_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "video_note":
            await bot.send_video_note(user_id, file_id, reply_markup=markup)
        elif item_type == "document":
            await bot.send_document(user_id, file_id, caption=caption, caption_entities=cap_entities, parse_mode=None, reply_markup=markup)
        elif item_type == "sticker":
            await bot.send_sticker(user_id, file_id, reply_markup=markup)
        return

    # Legacy fallback (old broadcasts without message_data)
    ct = broadcast["content_type"]
    text = broadcast["text"]
    file_id = broadcast["file_id"]

    if ct == "text":
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=markup)
    elif ct == "photo":
        await bot.send_photo(user_id, file_id, caption=text, parse_mode="HTML", reply_markup=markup)
    elif ct == "video":
        await bot.send_video(user_id, file_id, caption=text, parse_mode="HTML", reply_markup=markup)
    elif ct == "animation":
        await bot.send_animation(user_id, file_id, caption=text, parse_mode="HTML", reply_markup=markup)
    elif ct == "document":
        await bot.send_document(user_id, file_id, caption=text, parse_mode="HTML", reply_markup=markup)
