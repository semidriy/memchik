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
    get_all_displays, create_display, get_display,
    toggle_display, delete_display, get_display_views_count,
)
from bot.keyboards.admin import (
    display_menu, display_list, display_item,
    display_skip_kb, display_cancel_kb,
)
from bot.states.admin import DisplayStates

router = Router()


from bot.services.permissions import has_permission, PERM_DISPLAYS


async def is_admin(user_id: int) -> bool:
    return await has_permission(user_id, PERM_DISPLAYS)


@router.callback_query(F.data == "dsp:new")
async def cb_dsp_new(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(DisplayStates.waiting_name)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "📺 Введи название показа (для админки, не видно пользователям):",
        reply_markup=display_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "dsp:cancel")
async def cb_dsp_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("📺 Показы", reply_markup=display_menu())
    await callback.answer()


@router.message(DisplayStates.waiting_name)
async def handle_dsp_name(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not name:
        return
    data = await state.get_data()
    await state.update_data(name=name)
    await state.set_state(DisplayStates.waiting_media)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        f"Название: <b>{name}</b>\n\n"
        "Пришли картинку или GIF, который покажется юзеру.\n"
        "Или нажми «Пропустить», если показ только текстовый.",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )


@router.callback_query(F.data == "dsp:skip", DisplayStates.waiting_media)
async def cb_dsp_skip_media(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(file_id=None, file_type=None)
    await state.set_state(DisplayStates.waiting_caption)
    await callback.message.edit_text(
        "Введи текст показа (HTML поддерживается). Или «Пропустить».",
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )
    await callback.answer()


@router.message(DisplayStates.waiting_media, F.animation)
async def handle_dsp_animation(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.update_data(file_id=message.animation.file_id, file_type="animation")
    await state.set_state(DisplayStates.waiting_caption)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "🎬 GIF принят.\n\nВведи текст показа (HTML поддерживается). Или «Пропустить».",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )


@router.message(DisplayStates.waiting_media, F.photo)
async def handle_dsp_photo(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await state.update_data(file_id=message.photo[-1].file_id, file_type="photo")
    await state.set_state(DisplayStates.waiting_caption)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "🖼 Картинка принята.\n\nВведи текст показа (HTML поддерживается). Или «Пропустить».",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )


@router.message(DisplayStates.waiting_media)
async def handle_dsp_media_invalid(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "Нужна картинка или GIF. Или нажми «Пропустить».",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        reply_markup=display_skip_kb(),
    )


@router.callback_query(F.data == "dsp:skip", DisplayStates.waiting_caption)
async def cb_dsp_skip_caption(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(caption_text=None)
    await state.set_state(DisplayStates.waiting_button)
    await callback.message.edit_text(
        "Введи кнопку:\n<code>Текст кнопки - https://ссылка.com</code>\n\nИли «Пропустить».",
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )
    await callback.answer()


@router.message(DisplayStates.waiting_caption)
async def handle_dsp_caption(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    caption = (message.text or "").strip()
    if not caption:
        return
    data = await state.get_data()
    await state.update_data(caption_text=caption)
    await state.set_state(DisplayStates.waiting_button)
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.edit_message_text(
        "Введи кнопку:\n<code>Текст кнопки - https://ссылка.com</code>\n\nИли «Пропустить».",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )


@router.callback_query(F.data == "dsp:skip", DisplayStates.waiting_button)
async def cb_dsp_skip_button(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.update_data(button_text=None, button_url=None)
    await state.set_state(DisplayStates.waiting_sort_order)
    await callback.message.edit_text(
        "Введи порядковый номер показа (число, меньшее = раньше). По умолчанию <b>0</b>.",
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )
    await callback.answer()


@router.message(DisplayStates.waiting_button)
async def handle_dsp_button(message: Message, state: FSMContext):
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
            "Неверный формат. <code>Текст - https://ссылка</code>",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            parse_mode="HTML",
            reply_markup=display_skip_kb(),
        )
        return
    await state.update_data(button_text=btn_text.strip(), button_url=btn_url.strip())
    await state.set_state(DisplayStates.waiting_sort_order)
    await message.bot.edit_message_text(
        "Введи порядковый номер показа (число, меньшее = раньше). По умолчанию <b>0</b>.",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=display_skip_kb(),
    )


async def _finalize_display(bot, state: FSMContext, data: dict, sort_order: int):
    pool = get_pool()
    d = await create_display(
        pool,
        data["name"],
        data.get("caption_text"),
        data.get("file_id"),
        data.get("file_type"),
        data.get("button_text"),
        data.get("button_url"),
        sort_order,
    )
    await state.clear()

    media_label = "🎬 GIF" if d.get("file_type") == "animation" else ("🖼 фото" if d.get("file_type") == "photo" else "📝 только текст")
    caption_preview = (d.get("caption_text") or "—")[:50]
    btn_preview = f"{d.get('button_text')} | {d.get('button_url')}" if d.get("button_text") else "—"

    await bot.edit_message_text(
        f"📺 Показ создан!\n\n"
        f"Название: <b>{d['name']}</b>\n"
        f"Медиа: {media_label}\n"
        f"Текст: {caption_preview}\n"
        f"Кнопка: {btn_preview}\n"
        f"Порядок: {sort_order}",
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=display_menu(),
    )


@router.callback_query(F.data == "dsp:skip", DisplayStates.waiting_sort_order)
async def cb_dsp_skip_sort(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()
    await _finalize_display(callback.bot, state, data, 0)
    await callback.answer()


@router.message(DisplayStates.waiting_sort_order)
async def handle_dsp_sort(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    try:
        order = int((message.text or "").strip())
    except ValueError:
        await message.bot.edit_message_text(
            "Нужно число.",
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=display_skip_kb(),
        )
        return
    await _finalize_display(message.bot, state, data, order)


@router.callback_query(F.data == "dsp:list")
async def cb_dsp_list(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    pool = get_pool()
    displays = await get_all_displays(pool)
    if not displays:
        await callback.message.edit_text("Показов ещё нет", reply_markup=display_menu())
        await callback.answer()
        return
    await callback.message.edit_text("📺 Показы:", reply_markup=display_list(displays))
    await callback.answer()


@router.callback_query(F.data.startswith("dsp:view:"))
async def cb_dsp_view(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    dsp_id = int(callback.data.split(":")[2])
    pool = get_pool()
    d = await get_display(pool, dsp_id)
    if not d:
        await callback.answer("Не найдено", show_alert=True)
        return
    views = await get_display_views_count(pool, dsp_id)
    status = "🟢 Активно" if d["is_active"] else "🔴 Отключено"
    media_label = "🎬 GIF" if d.get("file_type") == "animation" else ("🖼 фото" if d.get("file_type") == "photo" else "📝 только текст")
    caption_preview = (d.get("caption_text") or "—")[:200]
    btn_info = f"{d.get('button_text')} → {d.get('button_url')}" if d.get("button_text") else "—"

    try:
        await callback.message.edit_text(
            f"<b>{d['name']}</b>\n\n"
            f"Статус: {status}\n"
            f"Медиа: {media_label}\n"
            f"Порядок: {d.get('sort_order', 0)}\n\n"
            f"📊 Уникальных просмотров: <b>{views}</b>\n\n"
            f"Текст:\n{caption_preview}\n\n"
            f"Кнопка: {btn_info}",
            parse_mode="HTML",
            reply_markup=display_item(dsp_id, d["is_active"]),
        )
    except TelegramBadRequest:
        pass
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("dsp:toggle:"))
async def cb_dsp_toggle(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    dsp_id = int(callback.data.split(":")[2])
    pool = get_pool()
    d = await get_display(pool, dsp_id)
    if not d:
        await callback.answer("Не найдено", show_alert=True)
        return
    new_state = not d["is_active"]
    await toggle_display(pool, dsp_id, new_state)
    await callback.answer("включено" if new_state else "отключено")
    await cb_dsp_view(callback)


@router.callback_query(F.data.startswith("dsp:delete:"))
async def cb_dsp_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    dsp_id = int(callback.data.split(":")[2])
    pool = get_pool()
    await delete_display(pool, dsp_id)
    await callback.message.edit_text("Показ удалён", reply_markup=display_menu())
    await callback.answer()
