from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_all_placements, create_placement,
    toggle_placement, delete_placement,
)
from bot.keyboards.admin import (
    placement_menu, placement_list, placement_item,
    placement_skip_kb, placement_duration_kb, placement_limit_skip_kb,
)
from bot.states.admin import PlacementStates

router = Router()


from bot.services.permissions import has_permission, PERM_PLACEMENTS


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_PLACEMENTS)


@router.callback_query(F.data == "plc:new")
async def cb_plc_new(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(PlacementStates.waiting_name)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "🪧 Введи название размещения (например: «Реклама Nike 15 мая»):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="plc:cancel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "plc:cancel")
async def cb_plc_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("🪧 Размещения рекламы", reply_markup=placement_menu())
    await callback.answer()


@router.message(PlacementStates.waiting_name)
async def handle_plc_name(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not name:
        return
    data = await state.get_data()
    await state.update_data(name=name)
    await state.set_state(PlacementStates.waiting_caption)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        f"Название: <b>{name}</b>\n\n"
        "Теперь введи текст рекламного сообщения, который будет отображаться под гифкой.\n"
        "Можно использовать HTML-теги (<b>жирный</b>, <i>курсив</i>, <a href='...'>ссылка</a>).\n\n"
        "Или нажми «Пропустить», если текст не нужен.",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=placement_skip_kb(),
    )


@router.callback_query(F.data == "plc:skip", PlacementStates.waiting_caption)
async def cb_plc_skip_caption(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(caption_text=None)
    await state.set_state(PlacementStates.waiting_button)
    await callback.message.edit_text(
        "Введи кнопку в формате:\n<code>Текст кнопки - https://ссылка.com</code>\n\n"
        "Или нажми «Пропустить», если кнопка не нужна.",
        parse_mode="HTML",
        reply_markup=placement_skip_kb(),
    )
    await callback.answer()


@router.message(PlacementStates.waiting_caption)
async def handle_plc_caption(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    caption = (message.text or "").strip()
    if not caption:
        return
    data = await state.get_data()
    await state.update_data(caption_text=caption)
    await state.set_state(PlacementStates.waiting_button)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "Введи кнопку в формате:\n<code>Текст кнопки - https://ссылка.com</code>\n\n"
        "Или нажми «Пропустить», если кнопка не нужна.",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=placement_skip_kb(),
    )


async def _ask_limit_count(bot, data: dict):
    await bot.edit_message_text(
        "📊 Сколько раз один юзер может отправить эту рекламу за окно?\n"
        "Введи число или нажми «По умолчанию» (3).",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=placement_limit_skip_kb(),
    )


async def _ask_limit_window(bot, data: dict):
    await bot.edit_message_text(
        "⏱ За сколько минут сбрасывается лимит?\n"
        "Введи число минут (например, <code>60</code> для часа) или нажми «По умолчанию» (60 мин).",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=placement_limit_skip_kb(),
    )


@router.callback_query(F.data == "plc:skip", PlacementStates.waiting_button)
async def cb_plc_skip_button(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(button_text=None, button_url=None)
    await state.set_state(PlacementStates.waiting_limit_count)
    data = await state.get_data()
    await _ask_limit_count(callback.bot, data)
    await callback.answer()


@router.message(PlacementStates.waiting_button)
async def handle_plc_button(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    if " - " in text:
        btn_text, btn_url = text.rsplit(" - ", 1)
    elif "|" in text:
        btn_text, btn_url = text.rsplit("|", 1)
    else:
        await message.bot.edit_message_text(
            "Неверный формат. Введи кнопку так:\n<code>Текст кнопки - https://ссылка.com</code>\n\nИли нажми «Пропустить».",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            parse_mode="HTML",
            reply_markup=placement_skip_kb(),
        )
        return
    await state.update_data(button_text=btn_text.strip(), button_url=btn_url.strip())
    await state.set_state(PlacementStates.waiting_limit_count)
    await _ask_limit_count(message.bot, data)


@router.callback_query(F.data == "plc:skip", PlacementStates.waiting_limit_count)
async def cb_plc_skip_limit_count(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(limit_count=3)
    await state.set_state(PlacementStates.waiting_limit_window)
    data = await state.get_data()
    await _ask_limit_window(callback.bot, data)
    await callback.answer()


@router.message(PlacementStates.waiting_limit_count)
async def handle_plc_limit_count(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    try:
        count = int((message.text or "").strip())
        if count < 1 or count > 1000:
            raise ValueError
    except ValueError:
        await message.bot.edit_message_text(
            "Нужно число от 1 до 1000.",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=placement_limit_skip_kb(),
        )
        return
    await state.update_data(limit_count=count)
    await state.set_state(PlacementStates.waiting_limit_window)
    await _ask_limit_window(message.bot, data)


@router.callback_query(F.data == "plc:skip", PlacementStates.waiting_limit_window)
async def cb_plc_skip_limit_window(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(limit_window_sec=3600)
    await state.set_state(PlacementStates.waiting_duration)
    await callback.message.edit_text(
        "Выбери продолжительность размещения:",
        reply_markup=placement_duration_kb(),
    )
    await callback.answer()


@router.message(PlacementStates.waiting_limit_window)
async def handle_plc_limit_window(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    try:
        minutes = int((message.text or "").strip())
        if minutes < 1 or minutes > 100000:
            raise ValueError
    except ValueError:
        await message.bot.edit_message_text(
            "Нужно число минут от 1 до 100000.",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=placement_limit_skip_kb(),
        )
        return
    await state.update_data(limit_window_sec=minutes * 60)
    await state.set_state(PlacementStates.waiting_duration)
    await message.bot.edit_message_text(
        "Выбери продолжительность размещения:",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        reply_markup=placement_duration_kb(),
    )


async def _finalize_placement(bot, state: FSMContext, data: dict, hours: int):
    starts_at = datetime.utcnow()
    ends_at = starts_at + timedelta(hours=hours)

    pool = get_pool()
    p = await create_placement(
        pool,
        data["name"],
        data.get("caption_text"),
        data.get("button_text"),
        data.get("button_url"),
        starts_at,
        ends_at,
        limit_count=data.get("limit_count", 3),
        limit_window_sec=data.get("limit_window_sec", 3600),
    )
    await state.clear()

    dur_label = f"{hours} ч" if hours < 24 else f"{hours // 24} дн"
    caption_preview = (data.get("caption_text") or "—")[:50]
    btn_preview = f"{data.get('button_text')} | {data.get('button_url')}" if data.get("button_text") else "—"
    limit_min = (data.get("limit_window_sec", 3600)) // 60

    await bot.edit_message_text(
        f"Размещение создано!\n\n"
        f"Название: <b>{p['name']}</b>\n"
        f"Текст: {caption_preview}\n"
        f"Кнопка: {btn_preview}\n"
        f"Лимит: {data.get('limit_count', 3)} раз / {limit_min} мин\n"
        f"Длительность: {dur_label}\n"
        f"Активно до: {ends_at.strftime('%d.%m.%Y %H:%M')} UTC",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=placement_menu(),
    )


@router.callback_query(F.data == "plc:dur:manual", PlacementStates.waiting_duration)
async def cb_plc_duration_manual(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(PlacementStates.waiting_duration_manual)
    await callback.message.edit_text(
        "✏️ Введи количество часов (например, <code>6</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="plc:cancel")],
        ]),
    )
    await callback.answer()


@router.message(PlacementStates.waiting_duration_manual)
async def handle_plc_duration_manual(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    try:
        hours = int((message.text or "").strip())
        if hours < 1 or hours > 8760:
            raise ValueError
    except ValueError:
        await message.bot.edit_message_text(
            "Нужно число часов от 1 до 8760.",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="plc:cancel")],
            ]),
        )
        return
    await _finalize_placement(message.bot, state, data, hours)


@router.callback_query(F.data.startswith("plc:dur:"), PlacementStates.waiting_duration)
async def cb_plc_duration(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    suffix = callback.data.split(":")[2]
    if suffix == "manual":
        return
    hours = int(suffix)
    data = await state.get_data()
    await _finalize_placement(callback.bot, state, data, hours)
    await callback.answer()


@router.callback_query(F.data == "plc:list")
async def cb_plc_list(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    pool = get_pool()
    placements = await get_all_placements(pool)
    if not placements:
        await callback.message.edit_text("Размещений ещё нет", reply_markup=placement_menu())
        await callback.answer()
        return
    await callback.message.edit_text("Размещения:", reply_markup=placement_list(placements))
    await callback.answer()


@router.callback_query(F.data.startswith("plc:view:"))
async def cb_plc_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    plc_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM ad_placements WHERE id = $1", plc_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    p = dict(row)
    status = "🟢 Активно" if p["is_active"] else "🔴 Отключено"
    now = datetime.utcnow()
    starts = p["starts_at"].replace(tzinfo=None)
    ends = p["ends_at"].replace(tzinfo=None)
    running = starts <= now <= ends
    time_status = "Идёт" if running else ("Ещё не началось" if now < starts else "Завершено")

    caption_preview = (p.get("caption_text") or "—")[:80]
    btn_info = f"{p.get('button_text')} → {p.get('button_url')}" if p.get("button_text") else "—"

    stats_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_sends,
            COUNT(DISTINCT user_id) AS unique_users,
            COUNT(DISTINCT chat_instance) FILTER (WHERE chat_instance IS NOT NULL) AS unique_chats
        FROM placement_sends WHERE placement_id = $1
        """,
        plc_id,
    )
    s = dict(stats_row) if stats_row else {"total_sends": 0, "unique_users": 0, "unique_chats": 0}
    limit_min = (p.get("limit_window_sec") or 3600) // 60

    await callback.message.edit_text(
        f"<b>{p['name']}</b>\n\n"
        f"Статус: {status} ({time_status})\n"
        f"Период: {p['starts_at'].strftime('%d.%m %H:%M')} — {p['ends_at'].strftime('%d.%m %H:%M')} UTC\n"
        f"Лимит: {p.get('limit_count', 3)} раз / {limit_min} мин на юзера\n\n"
        f"📊 Отправок: <b>{s['total_sends']}</b>  |  Уников: <b>{s['unique_users']}</b>  |  Чатов: <b>{s['unique_chats']}</b>\n\n"
        f"Текст:\n{caption_preview}\n\n"
        f"Кнопка: {btn_info}",
        parse_mode="HTML",
        reply_markup=placement_item(plc_id, p["is_active"]),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("plc:toggle:"))
async def cb_plc_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    plc_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT is_active FROM ad_placements WHERE id = $1", plc_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    new_state = not row["is_active"]
    await toggle_placement(pool, plc_id, new_state)
    await callback.answer("включено" if new_state else "отключено")
    await cb_plc_view(callback)


@router.callback_query(F.data.startswith("plc:delete:"))
async def cb_plc_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    plc_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await delete_placement(pool, plc_id)
    await callback.message.edit_text("Размещение удалено", reply_markup=placement_menu())
    await callback.answer()
