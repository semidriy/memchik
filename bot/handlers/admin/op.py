import aiohttp

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.database import get_pool
from bot.database.queries import (
    get_all_op_channels, add_op_channel,
    toggle_op_channel, delete_op_channel,
)
from bot.keyboards.admin import op_menu, op_list, op_channel_item
from bot.states.admin import OpStates

router = Router()

_TYPE_LABEL = {"channel": "📢 Канал", "bot": "🤖 Бот", "link": "🔗 Без проверки", "folder": "📁 Папка"}

_ADD_TEXT = (
    "Введите данные в формате:\n\n"
    "<code>Токен бота | ID канала</code>\n"
    "<code>Название кнопки</code>\n"
    "<code>Ссылка на бота / канал</code>\n"
    "<code>Отслеживать подписки (1 - да, 0 - нет)</code>\n"
    "<code>Лимит переходов (0 - без лимита)</code>\n\n"
    "Примеры:\n"
    "Если канал с проверкой подписки:\n"
    "<code> | -1001234567890\nМой канал\nhttps://t.me/mychannel\n1\n0</code>\n\n"
    "Если бот с токеном:\n"
    "<code>123456:TOKEN | @mybot\nМой бот\nhttps://t.me/mybot\n1\n100</code>\n\n"
    "Если ссылка без проверки:\n"
    "<code> | https://t.me/+invitecode\nЗакрытый канал\nhttps://t.me/+invitecode\n0\n0</code>"
)

_CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Отмена", callback_data="op:cancel_add")],
])


from bot.services.permissions import has_permission, PERM_OP


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_OP)


@router.callback_query(F.data == "op:list")
async def cb_op_list(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    pool = get_pool()
    channels = await get_all_op_channels(pool)
    if not channels:
        await callback.message.edit_text("Каналов ещё нет", reply_markup=op_menu())
        await callback.answer()
        return
    await callback.message.edit_text("Каналы ОП:", reply_markup=op_list(channels))
    await callback.answer()


@router.callback_query(F.data == "op:add")
async def cb_op_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(OpStates.waiting_input)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(_ADD_TEXT, parse_mode="HTML", reply_markup=_CANCEL_KB)
    await callback.answer()


@router.callback_query(F.data == "op:cancel_add")
async def cb_op_cancel_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("⭐️ ОП каналы", reply_markup=op_menu())
    await callback.answer()


def _normalize(text: str) -> str:
    text = text.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if text.lower().startswith(prefix):
            return "@" + text[len(prefix):]
    if not text.startswith("@"):
        return "@" + text
    return text


@router.message(OpStates.waiting_input)
async def handle_op_input(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass

    async def _err(text: str):
        await message.bot.edit_message_text(
            _ADD_TEXT + f"\n\n❌ {text}",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML", reply_markup=_CANCEL_KB,
        )

    lines = [l.strip() for l in (message.text or "").strip().split("\n")]
    if len(lines) < 5:
        await _err("Нужно 5 строк. Попробуй ещё раз.")
        return

    # Line 1: [token |] channel_id_or_username
    raw1 = lines[0]
    if "|" in raw1:
        token_raw, ch_raw = raw1.split("|", 1)
        token = token_raw.strip() or None
        ch_raw = ch_raw.strip()
    else:
        token = None
        ch_raw = raw1.strip()

    title = lines[1].strip()
    if not title:
        await _err("Название кнопки (строка 2) не может быть пустым.")
        return

    invite_link = lines[2].strip()

    check_sub = lines[3].strip()
    if check_sub not in ("0", "1"):
        await _err("Строка 4 должна быть 1 (проверять) или 0 (не проверять).")
        return

    try:
        limit_visits = int(lines[4].strip())
    except ValueError:
        await _err("Строка 5 должна быть числом (0 = без лимита).")
        return

    # Determine channel type and ID
    pool = get_pool()

    # Private link or folder
    ch_clean = ch_raw.replace("https://", "").replace("http://", "")
    if ch_clean.startswith("t.me/addlist/") or ch_raw.lower().startswith("tg://addlist"):
        await add_op_channel(pool, None, "", title, invite_link or ch_raw,
                             "folder", limit_visits=limit_visits)
        await state.clear()
        await message.bot.edit_message_text(
            f"✅ 📁 Папка <b>{title}</b> добавлена\n⚠️ Подписка не проверяется",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML", reply_markup=op_menu(),
        )
        return

    if ch_clean.startswith("t.me/+"):
        lnk = "https://" + ch_clean
        await add_op_channel(pool, None, "", title, invite_link or lnk,
                             "link", limit_visits=limit_visits)
        await state.clear()
        await message.bot.edit_message_text(
            f"✅ 🔗 <b>{title}</b> добавлен\n⚠️ Подписка не проверяется",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML", reply_markup=op_menu(),
        )
        return

    # Numeric channel ID
    ch_stripped = ch_raw.lstrip("@").strip()
    if ch_stripped.lstrip("-").isdigit():
        channel_id = int(ch_stripped)
        username = ""
        channel_type = "bot" if token else ("channel" if check_sub == "1" else "link")
        if token:
            valid = await _validate_token(token)
            if not valid:
                await _err("Токен бота невалиден. Проверь и попробуй снова.")
                return
        await add_op_channel(pool, channel_id, username, title, invite_link,
                             channel_type, bot_token=token, limit_visits=limit_visits)
        await state.clear()
        label = _TYPE_LABEL.get(channel_type, "Добавлено")
        await message.bot.edit_message_text(
            f"✅ {label} <b>{title}</b> добавлен (ID: <code>{channel_id}</code>)",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML", reply_markup=op_menu(),
        )
        return

    # @username — resolve via Telegram
    username_norm = _normalize(ch_raw)
    try:
        chat = await message.bot.get_chat(username_norm)
    except TelegramBadRequest:
        await _err(f"Канал/бот {username_norm} не найден.")
        return

    channel_id = chat.id
    uname = chat.username or ""
    channel_type = "bot" if token else ("channel" if check_sub == "1" else "link")

    if token:
        valid = await _validate_token(token)
        if not valid:
            await _err("Токен бота невалиден. Проверь и попробуй снова.")
            return

    final_link = invite_link or (f"https://t.me/{uname}" if uname else "")
    if not final_link and channel_type == "channel":
        try:
            lnk = await message.bot.create_chat_invite_link(channel_id)
            final_link = lnk.invite_link
        except Exception:
            pass

    await add_op_channel(pool, channel_id, uname, title, final_link,
                         channel_type, bot_token=token, limit_visits=limit_visits)
    await state.clear()
    label = _TYPE_LABEL.get(channel_type, "Добавлено")
    await message.bot.edit_message_text(
        f"✅ {label} <b>{title}</b> добавлен",
        chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML", reply_markup=op_menu(),
    )


async def _validate_token(token: str) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                result = await r.json()
                return result.get("ok", False)
    except Exception:
        return False


@router.callback_query(F.data.startswith("op:view:"))
async def cb_op_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    ch_db_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM op_channels WHERE id = $1", ch_db_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    ch = dict(row)
    status = "🟢 Активен" if ch["is_active"] else "🔴 Отключён"
    ch_type = _TYPE_LABEL.get(ch.get("channel_type", "channel"), "📢 Канал")
    verify = "" if ch.get("channel_type", "channel") == "channel" else "\n⚠️ Подписка не проверяется"
    limit = ch.get("limit_visits") or 0
    limit_str = f"\nЛимит: {limit}" if limit else ""
    await callback.message.edit_text(
        f"{ch_type}: <b>{ch['title']}</b>\n"
        f"ID: <code>{ch['channel_id'] or '—'}</code>\n"
        f"Username: {('@' + ch['channel_username']) if ch.get('channel_username') else '—'}\n"
        f"Статус: {status}{verify}{limit_str}",
        parse_mode="HTML",
        reply_markup=op_channel_item(ch_db_id, ch["title"], ch["is_active"]),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("op:toggle:"))
async def cb_op_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    ch_db_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT is_active FROM op_channels WHERE id = $1", ch_db_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    new_state = not row["is_active"]
    await toggle_op_channel(pool, ch_db_id, new_state)
    await callback.answer("включён" if new_state else "отключён")
    await cb_op_view(callback)


@router.callback_query(F.data.startswith("op:delete:"))
async def cb_op_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    ch_db_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await delete_op_channel(pool, ch_db_id)
    await callback.message.edit_text("🗑 Удалено", reply_markup=op_menu())
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("op:rename:"))
async def cb_op_rename(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    ch_db_id = int(callback.data.split(":")[2])
    await state.set_state(OpStates.waiting_rename)
    await state.update_data(ch_db_id=ch_db_id, pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "Введи новое название для кнопки канала:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"op:view:{ch_db_id}")],
        ]),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.message(OpStates.waiting_rename)
async def handle_op_rename(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    new_title = (message.text or "").strip()
    if not new_title:
        return
    data = await state.get_data()
    ch_db_id = data["ch_db_id"]
    pool = get_pool()
    await pool.execute("UPDATE op_channels SET title = $1 WHERE id = $2", new_title, ch_db_id)
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    row = await pool.fetchrow("SELECT * FROM op_channels WHERE id = $1", ch_db_id)
    if not row:
        await message.bot.edit_message_text("Не найдено", chat_id=data["pm_cid"],
                                             message_id=data["pm_mid"], reply_markup=op_menu())
        return
    ch = dict(row)
    status = "🟢 Активен" if ch["is_active"] else "🔴 Отключён"
    ch_type = _TYPE_LABEL.get(ch.get("channel_type", "channel"), "📢 Канал")
    verify = "" if ch.get("channel_type", "channel") == "channel" else "\n⚠️ Подписка не проверяется"
    limit = ch.get("limit_visits") or 0
    limit_str = f"\nЛимит: {limit}" if limit else ""
    await message.bot.edit_message_text(
        f"{ch_type}: <b>{ch['title']}</b>\n"
        f"ID: <code>{ch['channel_id'] or '—'}</code>\n"
        f"Username: {('@' + ch['channel_username']) if ch.get('channel_username') else '—'}\n"
        f"Статус: {status}{verify}{limit_str}",
        chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=op_channel_item(ch_db_id, ch["title"], ch["is_active"]),
    )
