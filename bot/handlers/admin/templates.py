import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    add_template, get_template, get_all_templates_admin,
    get_templates_count, toggle_template, delete_template,
    update_template_tags,
)
from bot.keyboards.admin import templates_menu, templates_list, template_item
from bot.services.query_parser import extract_emojis
from bot.states.admin import TemplateStates

router = Router()
PER_PAGE = 8

_CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="❌ Отмена", callback_data="tpl:cancel")],
])


from bot.services.permissions import has_permission, PERM_TEMPLATES


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_TEMPLATES)


@router.callback_query(F.data == "tpl:add")
async def cb_tpl_add(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(TemplateStates.waiting_media)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text("Отправь GIF или фото для шаблона.", reply_markup=_CANCEL_KB)
    await callback.answer()


@router.callback_query(F.data == "tpl:cancel")
async def cb_tpl_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    pool = get_pool()
    total = await get_templates_count(pool)
    await callback.message.edit_text(f"🎭 Шаблоны\nВсего: {total}", reply_markup=templates_menu())
    await callback.answer()


@router.message(TemplateStates.waiting_media)
async def handle_template_media(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    if message.animation:
        file_id, file_unique_id, file_type = (
            message.animation.file_id, message.animation.file_unique_id, "animation"
        )
    elif message.photo:
        file_id, file_unique_id, file_type = (
            message.photo[-1].file_id, message.photo[-1].file_unique_id, "photo"
        )
    elif message.document and message.document.mime_type and (
        message.document.mime_type.startswith("image/gif")
        or message.document.mime_type.startswith("video/")
    ):
        file_id, file_unique_id, file_type = (
            message.document.file_id, message.document.file_unique_id, "animation"
        )
    else:
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    await state.update_data(file_id=file_id, file_unique_id=file_unique_id, file_type=file_type)
    await state.set_state(TemplateStates.waiting_title)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "Напиши название шаблона:",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        reply_markup=_CANCEL_KB,
    )


@router.message(TemplateStates.waiting_title)
async def handle_template_title(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title or len(title) > 255:
        try:
            await message.delete()
        except Exception:
            pass
        return
    data = await state.get_data()
    await state.update_data(title=title)
    await state.set_state(TemplateStates.waiting_tags)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "Отправь эмодзи-теги (например: 👋 😂 🤔)\n\nТеги нужны для поиска через @бот",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⏭ Без тегов", callback_data="tpl:skip_tags"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="tpl:cancel"),
            ],
        ]),
    )


@router.callback_query(F.data == "tpl:skip_tags", TemplateStates.waiting_tags)
async def cb_tpl_skip_tags(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    await _finalize(callback.bot, state, [], data)
    await callback.answer()


@router.message(TemplateStates.waiting_tags)
async def handle_template_tags(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    tags = extract_emojis(message.text or "")
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    await _finalize(message.bot, state, tags, data)


async def _finalize(bot, state: FSMContext, tags: list[str], data: dict):
    pool = get_pool()
    template = await add_template(
        pool, data["file_id"], data["file_unique_id"], data["file_type"], data["title"], tags
    )
    await state.clear()
    tags_str = " ".join(tags) if tags else "нет"
    total = await get_templates_count(pool)
    await bot.edit_message_text(
        f"✅ Шаблон <b>{template['title']}</b> добавлен\n"
        f"Теги: {tags_str} | ID: {template['id']} | Всего: {total}",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить ещё", callback_data="tpl:add"),
                InlineKeyboardButton(text="📋 К списку", callback_data="tpl:list:0"),
            ],
        ]),
    )


async def _show_templates_list(message_or_callback, page: int):
    pool = get_pool()
    templates = await get_all_templates_admin(pool, limit=PER_PAGE, offset=page * PER_PAGE)
    total = await get_templates_count(pool)
    msg = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback

    if not templates and page == 0:
        text, kb = "Шаблонов ещё нет", templates_menu()
    else:
        text = f"📋 Шаблоны (всего {total}) — по популярности"
        kb = templates_list(templates, page, total, PER_PAGE)

    is_media = bool(msg.photo or msg.animation or msg.document or msg.video)
    if is_media:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, reply_markup=kb)
    else:
        try:
            await msg.edit_text(text, reply_markup=kb)
        except Exception:
            await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("tpl:list:"))
async def cb_tpl_list(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    page = int(callback.data.split(":")[2])
    await _show_templates_list(callback, page)
    await callback.answer()


@router.callback_query(F.data.startswith("tpl:view:"))
async def cb_tpl_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    template_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    pool = get_pool()
    t = await get_template(pool, template_id)
    if not t:
        await callback.answer("Не найдено", show_alert=True)
        return

    status = "🟢 Активен" if t["is_active"] else "🔴 Отключён"
    mod = t.get("moderation_status", "approved")
    if mod == "pending":
        status = "⏳ На модерации"
    elif mod == "rejected":
        status = "❌ Отклонён"
    icon = "🎬" if t["file_type"] == "animation" else "🖼"
    tags_str = " ".join(t["tags"]) if t["tags"] else "нет"
    pub_label = "Публичный" if t.get("is_public") else "👤 Личный"
    caption = (
        f"{icon} <b>{t['title'] or 'Без названия'}</b>\n\n"
        f"ID: {t['id']} | {pub_label}\n"
        f"Теги: {tags_str}\n"
        f"Использований: <b>{t['use_count']}</b>\n"
        f"Статус: {status}\n"
        f"Добавлен: {t['created_at'].strftime('%d.%m.%Y')}"
    )
    markup = template_item(template_id, t["is_active"], page)

    if t["file_type"] == "animation":
        await callback.message.answer_animation(t["file_id"], caption=caption, parse_mode="HTML", reply_markup=markup)
    else:
        await callback.message.answer_photo(t["file_id"], caption=caption, parse_mode="HTML", reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("tpl:toggle:"))
async def cb_tpl_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    template_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    pool = get_pool()
    t = await get_template(pool, template_id)
    if not t:
        await callback.answer("Не найдено", show_alert=True)
        return
    new_active = not t["is_active"]
    await toggle_template(pool, template_id, new_active)
    await callback.answer("включён" if new_active else "отключён")
    # Re-render caption in-place (message is the media message with caption)
    t = await get_template(pool, template_id)
    status = "🟢 Активен" if t["is_active"] else "🔴 Отключён"
    mod = t.get("moderation_status", "approved")
    if mod == "pending":
        status = "⏳ На модерации"
    elif mod == "rejected":
        status = "❌ Отклонён"
    icon = "🎬" if t["file_type"] == "animation" else "🖼"
    tags_str = " ".join(t["tags"]) if t["tags"] else "нет"
    pub_label = "Публичный" if t.get("is_public") else "👤 Личный"
    caption = (
        f"{icon} <b>{t['title'] or 'Без названия'}</b>\n\n"
        f"ID: {t['id']} | {pub_label}\n"
        f"Теги: {tags_str}\n"
        f"Использований: <b>{t['use_count']}</b>\n"
        f"Статус: {status}\n"
        f"Добавлен: {t['created_at'].strftime('%d.%m.%Y')}"
    )
    try:
        await callback.message.edit_caption(caption=caption, parse_mode="HTML", reply_markup=template_item(template_id, t["is_active"], page))
    except Exception:
        pass


@router.callback_query(F.data.startswith("tpl:tags:"))
async def cb_tpl_edit_tags(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    template_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    await state.set_state(TemplateStates.editing_tags)
    sent = await callback.message.answer(
        "Отправь новые эмодзи-теги (например: 👋 😂)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⏭ Убрать все теги", callback_data="tpl:clear_tags"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="tpl:cancel_tags"),
            ],
        ]),
    )
    await state.update_data(template_id=template_id, page=page, pm_cid=sent.chat.id, pm_mid=sent.message_id)
    await callback.answer()


@router.callback_query(F.data == "tpl:clear_tags", TemplateStates.editing_tags)
async def cb_tpl_clear_tags(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    await update_template_tags(get_pool(), data["template_id"], [])
    await state.clear()
    await callback.message.edit_text("✅ Теги убраны", reply_markup=templates_menu())
    await callback.answer()


@router.callback_query(F.data == "tpl:cancel_tags", TemplateStates.editing_tags)
async def cb_tpl_cancel_tags(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.delete()
    await callback.answer("Отменено")


@router.message(TemplateStates.editing_tags)
async def handle_edit_tags(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    template_id = data.get("template_id")
    if not template_id:
        logger.error("handle_edit_tags: template_id missing in state data=%s", data)
        await state.clear()
        return
    tags = extract_emojis(message.text or "")
    logger.info("update tags template_id=%s tags=%s", template_id, tags)
    await update_template_tags(get_pool(), template_id, tags)
    await state.clear()
    tags_str = " ".join(tags) if tags else "нет"
    try:
        await message.delete()
    except Exception:
        pass
    page = data.get("page", 0)
    await message.bot.edit_message_text(
        f"✅ Теги обновлены: {tags_str}",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К списку", callback_data=f"tpl:list:{page}")],
        ]),
    )


@router.callback_query(F.data.startswith("tpl:delete:"))
async def cb_tpl_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    template_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    pool = get_pool()
    t = await get_template(pool, template_id)
    if not t:
        await callback.answer("Не найдено", show_alert=True)
        return
    await delete_template(pool, template_id)
    await callback.message.edit_caption(
        caption=f"🗑 Шаблон «{t['title'] or 'Без названия'}» удалён",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 К списку", callback_data=f"tpl:list:{page}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
