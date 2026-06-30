import json
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, MessageEntity,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_all_bot_messages, get_bot_message, set_bot_message, set_bot_message_media,
    get_all_bot_buttons, get_bot_buttons, set_bot_buttons,
)

router = Router()


class MsgEditStates(StatesGroup):
    waiting_message = State()
    waiting_buttons = State()  # legacy, kept for safety


class BtnAddStates(StatesGroup):
    emoji = State()
    text = State()
    callback = State()
    color = State()


from bot.services.permissions import has_permission, PERM_MESSAGES


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_MESSAGES)


def _messages_list_kb(messages: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for m in messages:
        mark = "✅" if m["from_db"] else "📝"
        preview = m["text"][:30].replace("\n", " ")
        rows.append([InlineKeyboardButton(
            text=f"{mark} {m['key']}: {preview}…",
            callback_data=f"msg:edit:{m['key']}",
        )])
    rows.append([InlineKeyboardButton(text="🔘 Кнопки", callback_data="msg:buttons_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _buttons_list_kb(buttons: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for b in buttons:
        rows.append([InlineKeyboardButton(
            text=f"🔘 {b['key']}",
            callback_data=f"msg:btn_edit:{b['key']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="msg:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "msg:list")
async def cb_msg_list(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    pool = get_pool()
    messages = await get_all_bot_messages(pool)
    await callback.message.edit_text(
        "✉️ Сообщения бота\n\n✅ = изменено | 📝 = стандартное",
        reply_markup=_messages_list_kb(messages),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("msg:edit:"))
async def cb_msg_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    key = callback.data[9:]
    pool = get_pool()
    msg = await get_bot_message(pool, key)

    entities_preview = f"\n(Энтитей: {len(msg['entities'])})" if msg["entities"] else ""
    media_line = ""
    extra_rows = []
    if msg.get("file_id"):
        media_line = f"\n🎬 Медиа: <b>{msg.get('file_type') or 'media'}</b> прикреплено"
        extra_rows.append([InlineKeyboardButton(text="🗑 Убрать медиа", callback_data=f"msg:media_clear:{key}")])
    await state.set_state(MsgEditStates.waiting_message)
    await state.update_data(
        edit_key=key,
        pm_cid=callback.message.chat.id,
        pm_mid=callback.message.message_id,
    )
    await callback.message.edit_text(
        f"✉️ <b>{key}</b>{entities_preview}{media_line}\n\nТекущий текст:\n<blockquote>{msg['text'][:300]}</blockquote>\n\n"
        "Отправь новое сообщение (поддерживаются <b>любые энтити</b>, прем-эмодзи, форматирование).\n"
        "🎬 Чтобы добавить медиа в менюшку — пришли <b>видео / гиф / фото</b> (подпись станет текстом).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            *extra_rows,
            [InlineKeyboardButton(text="🔄 Сбросить к дефолту", callback_data=f"msg:reset:{key}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="msg:cancel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "msg:cancel")
async def cb_msg_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    pool = get_pool()
    messages = await get_all_bot_messages(pool)
    await callback.message.edit_text(
        "✉️ Сообщения бота\n\n✅ = изменено | 📝 = стандартное",
        reply_markup=_messages_list_kb(messages),
    )
    await callback.answer()


@router.message(MsgEditStates.waiting_message)
async def handle_msg_content(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    key = data.get("edit_key")
    if not key:
        await state.clear()
        return

    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    entities_json = [
        {k: v for k, v in {
            "type": e.type.value if hasattr(e.type, "value") else e.type,
            "offset": e.offset,
            "length": e.length,
            "url": e.url,
            "custom_emoji_id": e.custom_emoji_id,
        }.items() if v is not None}
        for e in entities
    ]

    # Если админ прислал видео/гиф/фото — прикрепляем как медиа менюшки.
    media_file_id, media_type = None, None
    if message.animation:
        media_file_id, media_type = message.animation.file_id, "animation"
    elif message.video:
        media_file_id, media_type = message.video.file_id, "video"
    elif message.photo:
        media_file_id, media_type = message.photo[-1].file_id, "photo"

    pool = get_pool()
    # Медиа БЕЗ подписи = «просто прикрепить медиа» → текст менюшки не трогаем.
    # Иначе сохраняем текст (это и подпись к медиа, если оно есть).
    if media_file_id and not text:
        await set_bot_message_media(pool, key, media_file_id, media_type)
    else:
        await set_bot_message(pool, key, text, entities_json)
        if media_file_id:
            await set_bot_message_media(pool, key, media_file_id, media_type)
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    media_note = f" 🎬 Медиа: {media_type}" if media_file_id else ""
    pool2 = get_pool()
    messages = await get_all_bot_messages(pool2)
    await message.bot.edit_message_text(
        f"✅ Сообщение <b>{key}</b> сохранено. Энтитей: {len(entities_json)}{media_note}\n\n"
        "✉️ Сообщения бота\n\n✅ = изменено | 📝 = стандартное",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=_messages_list_kb(messages),
    )


@router.callback_query(F.data.startswith("msg:reset:"))
async def cb_msg_reset(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    from bot.database.queries import _MSG_DEFAULTS
    key = callback.data[10:]
    if key in _MSG_DEFAULTS:
        pool = get_pool()
        await pool.execute("DELETE FROM bot_messages WHERE key = $1", key)
        await state.clear()
        messages = await get_all_bot_messages(pool)
        await callback.message.edit_text(
            f"✅ Сообщение <b>{key}</b> сброшено к дефолту\n\n"
            "✉️ Сообщения бота\n\n✅ = изменено | 📝 = стандартное",
            parse_mode="HTML",
            reply_markup=_messages_list_kb(messages),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("msg:media_clear:"))
async def cb_msg_media_clear(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    key = callback.data[len("msg:media_clear:"):]
    pool = get_pool()
    await set_bot_message_media(pool, key, None, None)
    await state.clear()
    messages = await get_all_bot_messages(pool)
    await callback.message.edit_text(
        f"✅ Медиа у <b>{key}</b> убрано.\n\n"
        "✉️ Сообщения бота\n\n✅ = изменено | 📝 = стандартное",
        parse_mode="HTML",
        reply_markup=_messages_list_kb(messages),
    )
    await callback.answer("Медиа убрано")


@router.callback_query(F.data == "msg:buttons_list")
async def cb_buttons_list(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    pool = get_pool()
    buttons = await get_all_bot_buttons(pool)
    await callback.message.edit_text(
        "🔘 Кнопки бота\n\nВыбери набор кнопок для редактирования:",
        reply_markup=_buttons_list_kb(buttons),
    )
    await callback.answer()


_STYLE_ICON = {"primary": "🔵", "success": "🟢", "danger": "🔴"}


def _btn_preview(rows: list) -> str:
    lines = []
    for row in rows:
        parts = []
        for b in row:
            icon = _STYLE_ICON.get(b.get("style"), "")
            emoji = b.get("icon_custom_emoji_id", "")
            emoji_mark = "✨" if emoji else ""
            parts.append(f"[{emoji_mark}{icon}{b['text']}]")
        lines.append(" ".join(parts))
    return "\n".join(lines) if lines else "(пусто)"


def _btn_edit_kb(key: str, rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить кнопку (новый ряд)", callback_data=f"msg:btn_add:{key}:new")],
        [InlineKeyboardButton(text="➕ Добавить в последний ряд", callback_data=f"msg:btn_add:{key}:same")],
        [InlineKeyboardButton(text="🗑 Удалить последнюю", callback_data=f"msg:btn_del:{key}")],
        [InlineKeyboardButton(text="✅ Сохранить", callback_data=f"msg:btn_save:{key}")],
        [InlineKeyboardButton(text="🔄 Сбросить всё", callback_data=f"msg:btn_reset:{key}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="msg:btn_cancel")],
    ])


async def _show_btn_editor(target, key: str, rows: list, state: FSMContext):
    """Show the button editor. target = CallbackQuery or (bot, chat_id, msg_id)."""
    text = f"🔘 <b>{key}</b>\n\nТекущие кнопки:\n{_btn_preview(rows)}"
    kb = _btn_edit_kb(key, rows)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await target.answer()
    else:
        bot, chat_id, msg_id = target
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id,
                                    parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("msg:btn_edit:"))
async def cb_btn_edit(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    key = callback.data[13:]
    pool = get_pool()
    rows = await get_bot_buttons(pool, key)
    await state.update_data(
        edit_key=key,
        edit_rows=rows,
        pm_cid=callback.message.chat.id,
        pm_mid=callback.message.message_id,
    )
    await _show_btn_editor(callback, key, rows, state)


@router.callback_query(F.data == "msg:btn_cancel")
async def cb_btn_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    pool = get_pool()
    buttons = await get_all_bot_buttons(pool)
    await callback.message.edit_text(
        "🔘 Кнопки бота\n\nВыбери набор кнопок для редактирования:",
        reply_markup=_buttons_list_kb(buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("msg:btn_del:"))
async def cb_btn_del(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    rows: list = list(data.get("edit_rows") or [])
    if rows:
        last_row = list(rows[-1])
        if last_row:
            last_row.pop()
        if last_row:
            rows[-1] = last_row
        else:
            rows.pop()
    key = data["edit_key"]
    await state.update_data(edit_rows=rows)
    await _show_btn_editor(callback, key, rows, state)


@router.callback_query(F.data.startswith("msg:btn_save:"))
async def cb_btn_save(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    key = data["edit_key"]
    rows = data.get("edit_rows") or []
    pool = get_pool()
    await set_bot_buttons(pool, key, rows)
    await state.clear()
    buttons = await get_all_bot_buttons(pool)
    await callback.message.edit_text(
        f"✅ Кнопки <b>{key}</b> сохранены!\n\n🔘 Кнопки бота:",
        parse_mode="HTML",
        reply_markup=_buttons_list_kb(buttons),
    )
    await callback.answer("Сохранено!")


# --- Step-by-step button adding ---

@router.callback_query(F.data.startswith("msg:btn_add:"))
async def cb_btn_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")  # msg:btn_add:KEY:new|same
    mode = parts[-1]  # "new" or "same"
    await state.set_state(BtnAddStates.emoji)
    await state.update_data(add_mode=mode)
    await callback.message.edit_text(
        "✨ <b>Шаг 1/4 — Прем-эмодзи (иконка)</b>\n\n"
        "Отправь сообщение содержащее прем-эмодзи — оно станет иконкой кнопки.\n"
        "Или нажми <b>Пропустить</b> если иконка не нужна.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="msg:btn_step:skip_emoji")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="msg:btn_step:cancel")],
        ]),
    )
    await callback.answer()


@router.message(BtnAddStates.emoji)
async def step_emoji(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    emoji_id = None
    for e in (message.entities or []):
        etype = e.type.value if hasattr(e.type, "value") else e.type
        if etype == "custom_emoji" and e.custom_emoji_id:
            emoji_id = e.custom_emoji_id
            break
    try:
        await message.delete()
    except Exception:
        pass
    await state.update_data(new_emoji_id=emoji_id)
    data = await state.get_data()
    await state.set_state(BtnAddStates.text)
    found = f"✅ Эмодзи найдено: <code>{emoji_id}</code>" if emoji_id else "⚠️ Прем-эмодзи не найдено, иконки не будет."
    await message.bot.edit_message_text(
        f"{found}\n\n"
        "✏️ <b>Шаг 2/4 — Текст кнопки</b>\n\nНапиши текст который будет на кнопке:",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="msg:btn_step:cancel")],
        ]),
    )


@router.callback_query(F.data == "msg:btn_step:skip_emoji")
async def step_skip_emoji(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(new_emoji_id=None)
    await state.set_state(BtnAddStates.text)
    await callback.message.edit_text(
        "✏️ <b>Шаг 2/4 — Текст кнопки</b>\n\nНапиши текст который будет на кнопке:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="msg:btn_step:cancel")],
        ]),
    )
    await callback.answer()


@router.message(BtnAddStates.text)
async def step_text(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.update_data(new_text=text)
    data = await state.get_data()
    await state.set_state(BtnAddStates.callback)
    await message.bot.edit_message_text(
        f"✅ Текст: <b>{text}</b>\n\n"
        "🔗 <b>Шаг 3/4 — Callback или ссылка</b>\n\n"
        "Напиши callback_data или URL:\n\n"
        "<code>meme:start</code> — галерея мемов\n"
        "<code>template:add_user</code> — добавить шаблон\n"
        "<code>utpl:quick</code> — личный шаблон (быстро)\n"
        "<code>utpl:public</code> — публичный шаблон\n"
        "<code>settings:open</code> — настройки\n"
        "<code>premium:open</code> — Гифыч Premium\n"
        "<code>https://t.me/...</code> — ссылка",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="msg:btn_step:cancel")],
        ]),
    )


@router.message(BtnAddStates.callback)
async def step_callback(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    cb = (message.text or "").strip()
    if not cb:
        return
    try:
        await message.delete()
    except Exception:
        pass
    await state.update_data(new_callback=cb)
    data = await state.get_data()
    await state.set_state(BtnAddStates.color)
    await message.bot.edit_message_text(
        f"✅ Callback: <code>{cb}</code>\n\n"
        "🎨 <b>Шаг 4/4 — Цвет кнопки</b>",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔵 Синий", callback_data="msg:btn_color:primary"),
                InlineKeyboardButton(text="🟢 Зелёный", callback_data="msg:btn_color:success"),
                InlineKeyboardButton(text="🔴 Красный", callback_data="msg:btn_color:danger"),
            ],
            [InlineKeyboardButton(text="⬜ Без цвета", callback_data="msg:btn_color:none")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="msg:btn_step:cancel")],
        ]),
    )


@router.callback_query(F.data.startswith("msg:btn_color:"))
async def step_color(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    color = callback.data[14:]  # primary / success / danger / none
    data = await state.get_data()

    entry: dict = {"text": data["new_text"]}
    cb_val = data["new_callback"]
    if cb_val.startswith("http://") or cb_val.startswith("https://") or cb_val.startswith("tg://"):
        entry["url"] = cb_val
    else:
        entry["callback_data"] = cb_val
    if color != "none":
        entry["style"] = color
    if data.get("new_emoji_id"):
        entry["icon_custom_emoji_id"] = data["new_emoji_id"]

    rows: list = list(data.get("edit_rows") or [])
    mode = data.get("add_mode", "new")
    if mode == "same" and rows:
        rows[-1] = list(rows[-1]) + [entry]
    else:
        rows.append([entry])

    key = data["edit_key"]
    await state.set_state(None)
    await state.update_data(edit_rows=rows, new_emoji_id=None, new_text=None, new_callback=None)

    await _show_btn_editor(
        (callback.bot, data["pm_cid"], data["pm_mid"]),
        key, rows, state,
    )
    await callback.answer("Кнопка добавлена!")


@router.callback_query(F.data == "msg:btn_step:cancel")
async def step_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    key = data.get("edit_key", "")
    rows = data.get("edit_rows") or []
    await state.set_state(None)
    await _show_btn_editor(callback, key, rows, state)


@router.callback_query(F.data.startswith("msg:btn_reset:"))
async def cb_btn_reset(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    key = callback.data[14:]
    pool = get_pool()
    await pool.execute("DELETE FROM bot_buttons WHERE key = $1", key)
    await state.update_data(edit_rows=[])
    await _show_btn_editor(callback, key, [], state)
    await callback.answer("Сброшено!")
    await callback.answer()
