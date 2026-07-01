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

_CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Отмена", callback_data="op:cancel_add")],
])

# Шаг 3 мастера: тип проверки (как в «кружочке»).
_CHECK_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ Проверка подписки", callback_data="op:ctype:sub")],
    [InlineKeyboardButton(text="⌛ Проверка заявки (закрытый)", callback_data="op:ctype:req")],
    [InlineKeyboardButton(text="🤖 Проверка запуска бота", callback_data="op:ctype:bot")],
    [InlineKeyboardButton(text="🚫 Без проверки (просто показать)", callback_data="op:ctype:none")],
    [InlineKeyboardButton(text="❌ Отмена", callback_data="op:cancel_add")],
])


from bot.services.permissions import has_permission, PERM_OP


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_OP)


async def _panel(message: Message, data: dict, text: str, kb: InlineKeyboardMarkup = _CANCEL_KB):
    """Перерисовываем сообщение-панель мастера (его id лежит в state), а не плодим новые."""
    await message.bot.edit_message_text(
        text, chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML", reply_markup=kb,
    )


def _public_username(url: str) -> str | None:
    """Из ссылки достаём публичный @username (для авто-определения ID канала).
    Для приватных инвайтов (t.me/+…, joinchat, addlist) возвращаем None."""
    u = (url or "").strip()
    low = u.lower()
    if "t.me/+" in low or "joinchat" in low or "addlist" in low or "tg://" in low:
        return None
    for p in ("https://t.me/", "http://t.me/", "t.me/"):
        if low.startswith(p):
            handle = u[len(p):].split("/")[0].split("?")[0]
            return handle or None
    if u.startswith("@"):
        return u[1:]
    return None


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


# ===== Список / просмотр =====

@router.callback_query(F.data == "op:list")
async def cb_op_list(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    pool = get_pool()
    channels = await get_all_op_channels(pool)
    if not channels:
        await callback.message.edit_text("Ресурсов ОП ещё нет", reply_markup=op_menu())
        await callback.answer()
        return
    await callback.message.edit_text("📋 Ресурсы ОП:", reply_markup=op_list(channels))
    await callback.answer()


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
    verify = "" if ch.get("channel_type", "channel") in ("channel", "bot") else "\n⚠️ Подписка не проверяется"
    limit = ch.get("limit_visits") or 0
    limit_str = f"\nЛимит переходов: {limit}" if limit else ""
    await callback.message.edit_text(
        f"{ch_type}: <b>{ch['title']}</b>\n"
        f"ID: <code>{ch['channel_id'] or '—'}</code>\n"
        f"Username: {('@' + ch['channel_username']) if ch.get('channel_username') else '—'}\n"
        f"Ссылка: {ch.get('invite_link') or '—'}\n"
        f"Статус: {status}{verify}{limit_str}",
        parse_mode="HTML",
        reply_markup=op_channel_item(ch_db_id, ch["title"], ch["is_active"]),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


# ===== Мастер добавления (как в KruzhokBot) =====

@router.callback_query(F.data == "op:add")
async def cb_op_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(OpStates.add_title)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "➕ <b>Добавление ресурса ОП</b>\n\n"
        "Шаг 1/4 — пришли <b>название кнопки</b> (что увидит пользователь):",
        parse_mode="HTML", reply_markup=_CANCEL_KB,
    )
    await callback.answer()


@router.callback_query(F.data == "op:cancel_add")
async def cb_op_cancel_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("⭐️ ОП ресурсы", reply_markup=op_menu())
    await callback.answer()


@router.message(OpStates.add_title)
async def w_title(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    if not title:
        await _panel(message, data, "Шаг 1/4 — название пустое. Пришли текст кнопки:")
        return
    await state.update_data(title=title)
    await state.set_state(OpStates.add_url)
    await _panel(
        message, data,
        f"✅ Название: <b>{title}</b>\n\n"
        "Шаг 2/4 — пришли <b>ссылку</b>:\n"
        "• открытый канал: <code>@username</code> или <code>https://t.me/username</code>\n"
        "• закрытый канал: инвайт-ссылка <code>https://t.me/+…</code>\n"
        "• бот: <code>https://t.me/yourbot</code>",
    )


@router.message(OpStates.add_url)
async def w_url(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    url = (message.text or "").strip()
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    if not url:
        await _panel(message, data, "Шаг 2/4 — ссылка пустая. Пришли ссылку:")
        return
    await state.update_data(url=url)
    await _panel(
        message, data,
        f"✅ Ссылка: {url}\n\nШаг 3/4 — выбери <b>тип проверки</b>:",
        kb=_CHECK_KB,
    )


@router.callback_query(F.data.startswith("op:ctype:"))
async def w_ctype(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    kind = callback.data.split(":")[2]  # sub | req | bot | none
    data = await state.get_data()
    if not data.get("title") or not data.get("url"):
        await state.clear()
        await callback.message.edit_text("Сессия сброшена, начни заново.", reply_markup=op_menu())
        await callback.answer()
        return
    pool = get_pool()
    title, url = data["title"], data["url"]

    if kind == "none":
        await add_op_channel(pool, None, "", title, url, "link")
        await state.clear()
        await callback.message.edit_text(
            f"✅ 🔗 <b>{title}</b> добавлен (без проверки)",
            parse_mode="HTML", reply_markup=op_menu(),
        )
        await callback.answer()
        return

    if kind == "bot":
        await state.set_state(OpStates.add_token)
        await callback.message.edit_text(
            "🤖 Шаг 4/4 — пришли <b>токен бота</b> (из @BotFather).\n"
            "Проверка засчитается, когда пользователь запустит этого бота.",
            parse_mode="HTML", reply_markup=_CANCEL_KB,
        )
        await callback.answer()
        return

    # sub / req → канал с проверкой. Открытый канал ID определим сами.
    username = _public_username(url)
    channel_id, uname = None, ""
    if username:
        try:
            chat = await callback.bot.get_chat("@" + username)
            channel_id, uname = chat.id, (chat.username or username)
        except Exception:
            channel_id = None

    if channel_id:
        await add_op_channel(pool, channel_id, uname, title, url, "channel")
        await state.clear()
        await callback.message.edit_text(
            f"✅ 📢 <b>{title}</b> добавлен\nID <code>{channel_id}</code> · проверка включена",
            parse_mode="HTML", reply_markup=op_menu(),
        )
        await callback.answer()
        return

    # Закрытый канал — ID сам не достанется, просим ввести.
    await state.set_state(OpStates.add_chatid)
    await callback.message.edit_text(
        "🔒 <b>Закрытый канал</b> — чтобы проверять заявку (как в «кружочке»):\n"
        "1) добавь бота <b>администратором</b> в канал;\n"
        "2) включи в канале режим <b>«Заявки на вступление»</b>;\n"
        "3) перешли любой пост из канала боту @userinfobot — он покажет ID (вида <code>-100…</code>).\n\n"
        "Шаг 4/4 — пришли этот <b>ID канала</b>:",
        parse_mode="HTML", reply_markup=_CANCEL_KB,
    )
    await callback.answer()


@router.message(OpStates.add_chatid)
async def w_chatid(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    if not raw.lstrip("-").isdigit():
        await _panel(message, data, "ID должен быть числом (вида <code>-1001234567890</code>). Пришли ещё раз:")
        return
    channel_id = int(raw)
    pool = get_pool()
    await add_op_channel(pool, channel_id, "", data["title"], data["url"], "channel")
    await state.clear()
    await message.bot.edit_message_text(
        f"✅ 🔒 <b>{data['title']}</b> добавлен\nID <code>{channel_id}</code> · проверка по заявке включена",
        chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML", reply_markup=op_menu(),
    )


@router.message(OpStates.add_token)
async def w_token(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    token = (message.text or "").strip()
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    if not await _validate_token(token):
        await _panel(message, data, "Токен невалиден. Проверь и пришли ещё раз:")
        return
    pool = get_pool()
    await add_op_channel(pool, None, "", data["title"], data["url"], "bot", bot_token=token)
    await state.clear()
    await message.bot.edit_message_text(
        f"✅ 🤖 <b>{data['title']}</b> добавлен (проверка запуска бота)",
        chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML", reply_markup=op_menu(),
    )


# ===== Управление ресурсом =====

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
    verify = "" if ch.get("channel_type", "channel") in ("channel", "bot") else "\n⚠️ Подписка не проверяется"
    limit = ch.get("limit_visits") or 0
    limit_str = f"\nЛимит переходов: {limit}" if limit else ""
    await message.bot.edit_message_text(
        f"{ch_type}: <b>{ch['title']}</b>\n"
        f"ID: <code>{ch['channel_id'] or '—'}</code>\n"
        f"Username: {('@' + ch['channel_username']) if ch.get('channel_username') else '—'}\n"
        f"Ссылка: {ch.get('invite_link') or '—'}\n"
        f"Статус: {status}{verify}{limit_str}",
        chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=op_channel_item(ch_db_id, ch["title"], ch["is_active"]),
    )
