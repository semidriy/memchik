from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
import logging

logger = logging.getLogger(__name__)

from bot.config import settings
from bot.keyboards.admin import (
    admin_reply_kb, admin_remove_kb,
    broadcast_menu, op_menu, links_menu, templates_menu, placement_menu, display_menu, admins_menu,
    languages_menu,
    BTN_BROADCAST, BTN_OP, BTN_LINKS, BTN_TEMPLATES,
    BTN_STATS, BTN_DATABASE, BTN_PLACEMENTS, BTN_DISPLAYS, BTN_ADMINS,
    BTN_LANGUAGES, BTN_CLOSE, ADMIN_BUTTONS, BTN_TO_PERM,
)
from bot.services.permissions import (
    is_admin as perm_is_admin, has_permission, get_user_permissions, is_super_admin,
)

router = Router()

# Tracks last admin-panel bot message per user_id → (chat_id, message_id).
# When the admin clicks a reply-keyboard button (or /admin), we delete the previous
# panel message + the user's button text, so the chat stays clean.
_LAST_PANEL_MSG: dict[int, tuple[int, int]] = {}


async def _send_panel(message: Message, text: str, reply_markup=None, **kwargs) -> Message:
    uid = message.from_user.id
    bot = message.bot

    prev = _LAST_PANEL_MSG.get(uid)
    if prev:
        try:
            await bot.delete_message(prev[0], prev[1])
        except Exception:
            pass

    sent = await bot.send_message(message.chat.id, text, reply_markup=reply_markup, **kwargs)
    _LAST_PANEL_MSG[uid] = (sent.chat.id, sent.message_id)
    return sent


async def is_admin(user_id: int) -> bool:
    return await perm_is_admin(user_id)


async def _allowed_buttons(user_id: int) -> set[str]:
    perms = set(await get_user_permissions(user_id))
    return {btn for btn, perm in BTN_TO_PERM.items() if perm in perms}


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.clear()
    allowed = await _allowed_buttons(message.from_user.id)
    # Не трогаем через _send_panel: к этому сообщению привязана reply-клавиатура.
    _LAST_PANEL_MSG.pop(message.from_user.id, None)
    await message.answer("Панель управления", reply_markup=admin_reply_kb(allowed))


@router.message(Command("fontcheck"))
async def cmd_fontcheck(message: Message):
    if not await is_admin(message.from_user.id):
        return
    from bot.services.overlay import _find_font_path
    path = _find_font_path()
    if path:
        await message.answer(f"✅ Шрифт найден:\n<code>{path}</code>", parse_mode="HTML")
    else:
        await message.answer(
            "❌ Шрифт <b>не найден</b> — пунктуация не отрисуется!\n\n"
            "Установи:\n<code>apt install fonts-dejavu-core</code>",
            parse_mode="HTML",
        )


@router.message(Command("dbcheck"))
async def cmd_dbcheck(message: Message):
    if not await is_admin(message.from_user.id):
        return
    from bot.database import get_pool
    pool = get_pool()
    try:
        total = await pool.fetchval("SELECT COUNT(*) FROM users")
        not_blocked = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = FALSE")
        last = await pool.fetchrow("SELECT id, first_name, created_at FROM users ORDER BY created_at DESC LIMIT 1")
        last_info = f"\nПоследний: {last['first_name']} (id={last['id']}, {last['created_at'].strftime('%d.%m %H:%M')})" if last else ""
        await message.answer(
            f"🗄 <b>Диагностика базы</b>\n\n"
            f"Всего пользователей: <b>{total}</b>\n"
            f"Активных (не заблокированы): <b>{not_blocked}</b>{last_info}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("dbcheck error: %s", e)
        await message.answer(f"❌ Ошибка БД: <code>{e}</code>", parse_mode="HTML")


@router.message(F.text.in_(ADMIN_BUTTONS))
async def handle_admin_button(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.clear()

    text = message.text
    uid = message.from_user.id

    if text == BTN_CLOSE:
        prev = _LAST_PANEL_MSG.pop(uid, None)
        if prev:
            try:
                await message.bot.delete_message(prev[0], prev[1])
            except Exception:
                pass
        await message.answer("Панель закрыта", reply_markup=admin_remove_kb())
        return

    needed = BTN_TO_PERM.get(text)
    if needed and not await has_permission(uid, needed):
        return

    if text == BTN_BROADCAST:
        await _send_panel(message, "📨 Рассылка", reply_markup=broadcast_menu())
    elif text == BTN_OP:
        await _send_panel(message, "⭐️ ОП каналы", reply_markup=op_menu())
    elif text == BTN_LINKS:
        await _send_panel(message, "🔗 Рекламные ссылки", reply_markup=links_menu())
    elif text == BTN_TEMPLATES:
        from bot.database import get_pool
        from bot.database.queries import get_templates_count
        total = await get_templates_count(get_pool())
        await _send_panel(message, f"🎭 Шаблоны\nВсего: {total}", reply_markup=templates_menu())
    elif text == BTN_STATS:
        from bot.handlers.admin.stats import send_placements_stats
        await send_placements_stats(message)
    elif text == BTN_DATABASE:
        from bot.handlers.admin.stats import send_db_stats
        await send_db_stats(message)
    elif text == BTN_PLACEMENTS:
        await _send_panel(message, "🪧 Размещения рекламы", reply_markup=placement_menu())
    elif text == BTN_DISPLAYS:
        await _send_panel(message, "📺 Показы", reply_markup=display_menu())
    elif text == BTN_ADMINS:
        await _send_panel(message, "👮 Управление админами", reply_markup=admins_menu())
    elif text == BTN_LANGUAGES:
        await _send_panel(message, "🌐 Языки", reply_markup=languages_menu())


@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("📨 Рассылка", reply_markup=broadcast_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:op")
async def cb_op_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("🔐 ОП каналы", reply_markup=op_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:links")
async def cb_links_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("🔗 Рекламные ссылки", reply_markup=links_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:templates")
async def cb_templates_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    from bot.database import get_pool
    from bot.database.queries import get_templates_count
    total = await get_templates_count(get_pool())
    await callback.message.edit_text(f"🎭 Шаблоны\nВсего: {total}", reply_markup=templates_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:placements")
async def cb_placements_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("🪧 Размещения рекламы", reply_markup=placement_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:displays")
async def cb_displays_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("📺 Показы", reply_markup=display_menu())
    await callback.answer()


@router.callback_query(F.data == "admin:admins")
async def cb_admins_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("👮 Управление админами", reply_markup=admins_menu())
    await callback.answer()
