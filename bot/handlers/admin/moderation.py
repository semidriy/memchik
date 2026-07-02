import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_template, approve_template, reject_template, delete_template,
)
from bot.keyboards.admin import moderation_kb
from bot.services.i18n import t as i18n_t, user_lang
from bot.services.permissions import has_permission, PERM_MODERATION
from bot.states.admin import ModerationStates

logger = logging.getLogger(__name__)

router = Router()


async def _edit_mod_caption(callback: CallbackQuery, suffix: str):
    """Append suffix to caption and strip buttons. Survives missing caption."""
    base = callback.message.caption or ""
    try:
        await callback.message.edit_caption(
            caption=(base + suffix)[:1024],
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception as e:
        logger.warning("edit_caption failed: %s", e)


@router.callback_query(F.data.startswith("mod:approve:"))
async def cb_approve(callback: CallbackQuery):
    logger.info("mod:approve from uid=%s data=%s", callback.from_user.id, callback.data)
    if not await has_permission(callback.from_user.id, PERM_MODERATION):
        await callback.answer("⛔ Нет прав на модерацию", show_alert=True)
        return
    template_id = int(callback.data.split(":")[2])
    pool = get_pool()
    t = await get_template(pool, template_id)
    if not t:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await approve_template(pool, template_id)
    await _edit_mod_caption(callback, "\n\n✅ Одобрено")
    await callback.answer("✅ Одобрено")
    if t.get("submitted_by"):
        try:
            lang = await user_lang(t["submitted_by"])
            await callback.bot.send_message(
                t["submitted_by"],
                await i18n_t("mod.approved_user", lang),
            )
        except Exception:
            pass
    if settings.public_channel_id:
        try:
            bot_info = await callback.bot.get_me()
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="➕ Добавить шаблон", url=f"https://t.me/{bot_info.username}?start=tpl_{template_id}")
            ]])
            if t["file_type"] == "animation":
                await callback.bot.send_animation(
                    settings.public_channel_id, t["file_id"],
                    reply_markup=kb,
                )
            else:
                await callback.bot.send_photo(
                    settings.public_channel_id, t["file_id"],
                    reply_markup=kb,
                )
        except Exception:
            pass


@router.callback_query(F.data.startswith("mod:delete:"))
async def cb_delete_template(callback: CallbackQuery):
    logger.info("mod:delete from uid=%s data=%s", callback.from_user.id, callback.data)
    if not await has_permission(callback.from_user.id, PERM_MODERATION):
        await callback.answer("⛔ Нет прав на модерацию", show_alert=True)
        return
    template_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await delete_template(pool, template_id)
    await _edit_mod_caption(callback, "\n\n🗑 Удалено")
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("mod:reject:"))
async def cb_reject(callback: CallbackQuery):
    logger.info("mod:reject from uid=%s data=%s", callback.from_user.id, callback.data)
    if not await has_permission(callback.from_user.id, PERM_MODERATION):
        await callback.answer("⛔ Нет прав на модерацию", show_alert=True)
        return
    template_id = int(callback.data.split(":")[2])
    pool = get_pool()
    t = await get_template(pool, template_id)
    if not t:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await reject_template(pool, template_id)
    await _edit_mod_caption(callback, "\n\n❌ Отклонено")
    await callback.answer("❌ Отклонено")
    if t.get("submitted_by"):
        try:
            lang = await user_lang(t["submitted_by"])
            await callback.bot.send_message(
                t["submitted_by"],
                await i18n_t("mod.rejected_user", lang),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=await i18n_t("tpl.add_another", lang), callback_data="template:add_user"),
                ]]),
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("mod:comment:"))
async def cb_reject_comment(callback: CallbackQuery, state: FSMContext):
    logger.info("mod:comment from uid=%s data=%s", callback.from_user.id, callback.data)
    if not await has_permission(callback.from_user.id, PERM_MODERATION):
        await callback.answer("⛔ Нет прав на модерацию", show_alert=True)
        return
    template_id = int(callback.data.split(":")[2])

    is_channel = callback.message.chat.type in ("channel", "supergroup", "group")
    admin_id = callback.from_user.id

    if is_channel:
        try:
            sent = await callback.bot.send_message(
                admin_id,
                f"✏️ Напиши причину отклонения шаблона #{template_id}:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="mod:cancel_comment")],
                ]),
            )
        except Exception as e:
            logger.warning("can't DM moderator %s: %s", admin_id, e)
            await callback.answer(
                "⚠️ Не могу написать в ЛС. Сначала запусти бота в личке.",
                show_alert=True,
            )
            return
        dm_key = StorageKey(
            bot_id=callback.bot.id, chat_id=admin_id, user_id=admin_id,
        )
        dm_state = FSMContext(storage=state.storage, key=dm_key)
        await dm_state.set_state(ModerationStates.waiting_reject_comment)
        await dm_state.update_data(
            template_id=template_id,
            orig_cid=callback.message.chat.id,
            orig_mid=callback.message.message_id,
            pm_cid=sent.chat.id,
            pm_mid=sent.message_id,
        )
        await callback.answer("Написал тебе в ЛС")
        return

    await state.set_state(ModerationStates.waiting_reject_comment)
    await state.update_data(
        template_id=template_id,
        orig_cid=callback.message.chat.id,
        orig_mid=callback.message.message_id,
    )
    sent = await callback.message.answer(
        "✏️ Напиши причину отклонения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="mod:cancel_comment")],
        ]),
    )
    await state.update_data(pm_cid=sent.chat.id, pm_mid=sent.message_id)
    await callback.answer()


@router.callback_query(F.data == "mod:cancel_comment", ModerationStates.waiting_reject_comment)
async def cb_cancel_comment(callback: CallbackQuery, state: FSMContext):
    if not await has_permission(callback.from_user.id, PERM_MODERATION):
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Отменено")


@router.message(ModerationStates.waiting_reject_comment)
async def handle_reject_comment(message: Message, state: FSMContext):
    if not await has_permission(message.from_user.id, PERM_MODERATION):
        return
    data = await state.get_data()
    template_id = data["template_id"]
    comment = (message.text or "").strip()
    await state.clear()

    pool = get_pool()
    t = await get_template(pool, template_id)
    if not t:
        try:
            await message.bot.edit_message_text(
                "Шаблон не найден",
                chat_id=data["pm_cid"],
                message_id=data["pm_mid"],
            )
        except Exception:
            pass
        return

    await reject_template(pool, template_id, comment)

    try:
        await message.bot.edit_message_caption(
            chat_id=data["orig_cid"],
            message_id=data["orig_mid"],
            caption=f"❌ Отклонено: {comment[:200]}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    try:
        await message.bot.edit_message_text(
            "❌ Отклонено. Причина отправлена пользователю.",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
        )
    except Exception:
        pass

    try:
        await message.delete()
    except Exception:
        pass

    if t.get("submitted_by"):
        try:
            lang = await user_lang(t["submitted_by"])
            await message.bot.send_message(
                t["submitted_by"],
                await i18n_t("mod.rejected_reason_user", lang, reason=comment),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=await i18n_t("tpl.add_another", lang), callback_data="template:add_user"),
                ]]),
            )
        except Exception:
            pass
