from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    list_bot_admins, get_admin_permissions,
    add_bot_admin, update_admin_permissions, remove_bot_admin,
)
from bot.keyboards.admin import (
    admins_menu, admins_list, admins_cancel_kb, admin_perms_kb,
)
from bot.services.permissions import (
    has_permission, is_super_admin, ALL_PERMISSIONS, PERM_LABELS, PERM_MANAGE_ADMINS,
)
from bot.states.admin import AdminMgmtStates

router = Router()


async def _can(uid: int) -> bool:
    return await has_permission(uid, PERM_MANAGE_ADMINS)


def _safe_edit_kwargs(callback: CallbackQuery) -> dict:
    return {"chat_id": callback.message.chat.id, "message_id": callback.message.message_id}


@router.callback_query(F.data == "adm:list")
async def cb_list(callback: CallbackQuery, state: FSMContext):
    if not await _can(callback.from_user.id):
        return
    await state.clear()
    pool = get_pool()
    admins = await list_bot_admins(pool)
    text = (
        f"👮 <b>Админы</b>\n\n"
        f"👑 super-админы: <b>{len(settings.admin_ids)}</b> (из .env, неудаляемые)\n"
        f"👤 динамические: <b>{len(admins)}</b>"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=admins_list(admins, list(settings.admin_ids)),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "adm:new")
async def cb_new(callback: CallbackQuery, state: FSMContext):
    if not await _can(callback.from_user.id):
        return
    await state.set_state(AdminMgmtStates.waiting_user)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "➕ <b>Новый админ</b>\n\n"
        "Отправь <b>Telegram ID</b> пользователя (число) или перешли его сообщение.\n"
        "После этого выберешь права чекбоксами.",
        parse_mode="HTML",
        reply_markup=admins_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:cancel", AdminMgmtStates.waiting_user)
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("👮 Управление админами", reply_markup=admins_menu())
    await callback.answer("Отменено")


@router.message(AdminMgmtStates.waiting_user)
async def handle_user_input(message: Message, state: FSMContext):
    if not await _can(message.from_user.id):
        return
    data = await state.get_data()
    pm_cid, pm_mid = data.get("pm_cid"), data.get("pm_mid")

    target_id = None
    if message.forward_from:
        target_id = message.forward_from.id
    elif message.text:
        try:
            target_id = int(message.text.strip())
        except ValueError:
            pass

    try:
        await message.delete()
    except Exception:
        pass

    if not target_id:
        try:
            await message.bot.edit_message_text(
                "❌ Не понял. Отправь число (Telegram ID) или перешли сообщение.",
                chat_id=pm_cid, message_id=pm_mid,
                reply_markup=admins_cancel_kb(),
            )
        except TelegramBadRequest:
            pass
        return

    if target_id in settings.admin_ids:
        await state.clear()
        try:
            await message.bot.edit_message_text(
                f"⚠️ Пользователь <code>{target_id}</code> уже супер-админ (из .env).\n\n👮 Управление админами",
                chat_id=pm_cid, message_id=pm_mid,
                parse_mode="HTML",
                reply_markup=admins_menu(),
            )
        except TelegramBadRequest:
            pass
        return

    pool = get_pool()
    existing = await get_admin_permissions(pool, target_id)
    if existing is None:
        await add_bot_admin(pool, target_id, [], message.from_user.id)
        existing = []
    await state.clear()

    try:
        await message.bot.edit_message_text(
            f"👤 Админ <code>{target_id}</code>\n\nВыбери права:",
            chat_id=pm_cid, message_id=pm_mid,
            parse_mode="HTML",
            reply_markup=admin_perms_kb(target_id, set(existing), ALL_PERMISSIONS, PERM_LABELS),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("adm:view:"))
async def cb_view(callback: CallbackQuery, state: FSMContext):
    if not await _can(callback.from_user.id):
        return
    await state.clear()
    target_id = int(callback.data.split(":")[2])
    pool = get_pool()
    perms = await get_admin_permissions(pool, target_id) or []
    try:
        await callback.message.edit_text(
            f"👤 Админ <code>{target_id}</code>\n\nВыбери права:",
            parse_mode="HTML",
            reply_markup=admin_perms_kb(target_id, set(perms), ALL_PERMISSIONS, PERM_LABELS),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("adm:toggle:"))
async def cb_toggle(callback: CallbackQuery):
    if not await _can(callback.from_user.id):
        return
    _, _, sid, perm = callback.data.split(":", 3)
    target_id = int(sid)
    if perm not in ALL_PERMISSIONS:
        await callback.answer("Неизвестное право", show_alert=True)
        return
    pool = get_pool()
    current = await get_admin_permissions(pool, target_id)
    if current is None:
        await callback.answer("Админ не найден", show_alert=True)
        return
    cur_set = set(current)
    if perm in cur_set:
        cur_set.discard(perm)
    else:
        cur_set.add(perm)
    new_perms = [p for p in ALL_PERMISSIONS if p in cur_set]
    await update_admin_permissions(pool, target_id, new_perms)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=admin_perms_kb(target_id, set(new_perms), ALL_PERMISSIONS, PERM_LABELS),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("✅" if perm in cur_set else "⬜")


@router.callback_query(F.data.startswith("adm:remove:"))
async def cb_remove(callback: CallbackQuery, state: FSMContext):
    if not await _can(callback.from_user.id):
        return
    target_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await remove_bot_admin(pool, target_id)
    admins = await list_bot_admins(pool)
    text = (
        f"🗑 Админ <code>{target_id}</code> удалён.\n\n"
        f"👑 super-админы: <b>{len(settings.admin_ids)}</b>\n"
        f"👤 динамические: <b>{len(admins)}</b>"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=admins_list(admins, list(settings.admin_ids)),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Удалён")


@router.callback_query(F.data == "adm:noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer("Супер-админ настраивается через .env", show_alert=True)
