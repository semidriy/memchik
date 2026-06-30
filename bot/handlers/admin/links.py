import random
import string
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    create_ad_link, get_all_ad_links, get_link_stats, get_link_clicks_by_day,
    toggle_ad_link, delete_ad_link,
)
from bot.keyboards.admin import links_menu, links_list, link_item, link_delete_confirm
from bot.states.admin import LinkStates

router = Router()

_SPARK = "▁▂▃▄▅▆▇█"


def _spark(values) -> str:
    vals = [int(v or 0) for v in values]
    hi = max(vals) if vals else 0
    if hi == 0:
        return _SPARK[0] * len(vals)
    return "".join(_SPARK[min(7, round(v / hi * 7))] for v in vals)


from bot.services.permissions import has_permission, PERM_LINKS


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_LINKS)


def _gen_code(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


@router.callback_query(F.data == "links:new")
async def cb_links_new(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await state.set_state(LinkStates.waiting_name)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "Введи название ссылки (например: «ТГ канал вася»):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="links:cancel_new")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "links:cancel_new")
async def cb_links_cancel_new(callback, state: FSMContext):
    from bot.config import settings as _s
    if callback.from_user.id not in _s.admin_ids:
        return
    await state.clear()
    await callback.message.edit_text("🔗 Рекламные ссылки", reply_markup=links_menu())
    await callback.answer()


@router.message(LinkStates.waiting_name)
async def handle_link_name(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    pool = get_pool()
    code = _gen_code()

    while True:
        existing = await pool.fetchval("SELECT id FROM ad_links WHERE code = $1", code)
        if not existing:
            break
        code = _gen_code()

    bot_info = await message.bot.get_me()
    link = await create_ad_link(pool, (message.text or "").strip(), code)
    deep_link = f"https://t.me/{bot_info.username}?start=ref_{code}"
    data = await state.get_data()
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    await message.bot.edit_message_text(
        f"✅ Ссылка создана!\n\n"
        f"Название: <b>{link['name']}</b>\n"
        f"Ссылка: <code>{deep_link}</code>\n"
        f"Код: <code>{code}</code>",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=links_menu(),
    )


@router.callback_query(F.data == "links:list")
async def cb_links_list(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    pool = get_pool()
    links = await get_all_ad_links(pool)
    if not links:
        await callback.message.edit_text("Ссылок ещё нет", reply_markup=links_menu())
        await callback.answer()
        return
    await callback.message.edit_text("Выбери ссылку:", reply_markup=links_list(links))
    await callback.answer()


@router.callback_query(F.data.startswith("links:view:"))
async def cb_link_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    link_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM ad_links WHERE id = $1", link_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return

    bot_info = await callback.bot.get_me()
    link = dict(row)
    deep_link = f"https://t.me/{bot_info.username}?start=ref_{link['code']}"
    status = "🟢 Активна" if link["is_active"] else "🔴 Отключена"

    await callback.message.edit_text(
        f"🔗 <b>{link['name']}</b>\n\n"
        f"Статус: {status}\n"
        f"Ссылка: <code>{deep_link}</code>\n"
        f"Создана: {link['created_at'].strftime('%d.%m.%Y %H:%M')}",
        parse_mode="HTML",
        reply_markup=link_item(link_id, link["is_active"]),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("links:stats:"))
async def cb_link_stats(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    link_id = int(callback.data.split(":")[2])
    pool = get_pool()

    row = await pool.fetchrow("SELECT name, is_active FROM ad_links WHERE id = $1", link_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return

    stats = await get_link_stats(pool, link_id)
    by_day = await get_link_clicks_by_day(pool, link_id, 7)
    unique_live = stats["unique_live"] or 0
    unique_clicks = stats["unique_clicks"] or 0
    total_clicks = stats["total_clicks"] or 0
    male = stats["male_unique"] or 0
    female = stats["female_unique"] or 0
    male_pct = round(male / unique_live * 100) if unique_live else 0
    female_pct = round(female / unique_live * 100) if unique_live else 0
    op_pct = round(stats["op_passed"] / unique_live * 100) if unique_live else 0
    # Доля «живых» среди уникальных и доля ботов среди всех переходов.
    live_pct = round(stats["live_clicks"] / total_clicks * 100) if total_clicks else 0
    bot_pct = round(stats["bot_clicks"] / total_clicks * 100) if total_clicks else 0
    repeat = total_clicks - unique_clicks  # повторные переходы
    day_max = max([r["cnt"] for r in by_day], default=0)

    text = (
        f"📊 Статистика: <b>{row['name']}</b>\n\n"
        f"Переходы всего: <b>{total_clicks}</b>  ·  уникальные: <b>{unique_clicks}</b>\n"
        f"Повторные: <b>{repeat}</b>\n"
        f"📅 Сегодня: <b>{stats['today_clicks']}</b>  ·  вчера: <b>{stats['yesterday_clicks']}</b>\n"
        f"<code>{_spark([r['cnt'] for r in by_day])}</code>  (макс/день {day_max})\n\n"
        f"👤 Живые: <b>{stats['live_clicks']}</b> ({live_pct}%)  ·  "
        f"🤖 боты: <b>{stats['bot_clicks']}</b> ({bot_pct}%)\n"
        f"✅ Прошли ОП: <b>{stats['op_passed']}</b> ({op_pct}% от живых уник.)\n\n"
        f"👫 Пол (уник. живые):\n"
        f"  М: {male} ({male_pct}%)  ·  Ж: {female} ({female_pct}%)"
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=link_item(link_id, row["is_active"]),
    )
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("links:toggle:"))
async def cb_link_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    link_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT is_active FROM ad_links WHERE id = $1", link_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return
    new_state = not row["is_active"]
    await toggle_ad_link(pool, link_id, new_state)
    status = "активирована" if new_state else "деактивирована"
    await callback.answer(f"Ссылка {status}")
    await cb_link_view(callback)


@router.callback_query(F.data.startswith("links:delete:") & ~F.data.contains("confirm"))
async def cb_link_delete_ask(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    link_id = int(callback.data.split(":")[2])
    pool = get_pool()
    row = await pool.fetchrow("SELECT name FROM ad_links WHERE id = $1", link_id)
    if not row:
        await callback.answer("Не найдено", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 Удалить ссылку <b>{row['name']}</b>?\n\nЭто действие необратимо.",
        parse_mode="HTML",
        reply_markup=link_delete_confirm(link_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("links:delete_confirm:"))
async def cb_link_delete_confirm(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    link_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await delete_ad_link(pool, link_id)
    links = await get_all_ad_links(pool)
    if not links:
        await callback.message.edit_text("🗑 Ссылка удалена", reply_markup=links_menu())
    else:
        from bot.keyboards.admin import links_list as links_list_kb
        await callback.message.edit_text("🗑 Ссылка удалена\n\nВсе ссылки:", reply_markup=links_list_kb(links))
    await callback.answer("Удалено")
