import random
import string

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import add_user_template, get_bot_message, get_bot_buttons
from bot.keyboards.user import build_inline_kb, main_menu
from bot.services.i18n import t, user_lang
from bot.states.admin import UserTemplateStates


def _random_title() -> str:
    return "Шаблон-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))

router = Router()


def _cancel_kb(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="utpl:cancel")],
    ])


@router.callback_query(F.data == "template:add_user")
async def cb_add_user_template(callback: CallbackQuery, state: FSMContext):
    lang = await user_lang(callback.from_user.id)
    await state.set_state(UserTemplateStates.choosing_type)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        await t("tpl.add_choose_type", lang),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("tpl.add_quick", lang), callback_data="utpl:quick")],
            [InlineKeyboardButton(text=await t("tpl.add_public", lang), callback_data="utpl:public")],
            [InlineKeyboardButton(text=await t("common.cancel", lang), callback_data="utpl:cancel")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"utpl:quick", "utpl:public"}))
async def cb_choose_type(callback: CallbackQuery, state: FSMContext):
    lang = await user_lang(callback.from_user.id)
    is_public = callback.data == "utpl:public"
    await state.update_data(is_public=is_public)

    data = await state.get_data()
    if data.get("file_id"):
        await _process_chosen_media(callback.bot, callback.from_user.id, state, lang)
        await callback.answer()
        return

    await state.set_state(UserTemplateStates.waiting_media)
    await callback.message.edit_text(
        await t("tpl.send_media", lang),
        reply_markup=_cancel_kb(await t("common.cancel", lang)),
    )
    await callback.answer()


def _extract_media(message: Message):
    if message.animation:
        return message.animation.file_id, message.animation.file_unique_id, "animation"
    if message.photo:
        return message.photo[-1].file_id, message.photo[-1].file_unique_id, "photo"
    if message.document and message.document.mime_type and (
        message.document.mime_type.startswith("image/gif")
        or message.document.mime_type.startswith("video/")
    ):
        return message.document.file_id, message.document.file_unique_id, "animation"
    return None


async def _process_chosen_media(bot, user_id: int, state: FSMContext, lang: str):
    data = await state.get_data()
    file_id = data["file_id"]
    file_unique_id = data["file_unique_id"]
    file_type = data["file_type"]
    is_public = data.get("is_public", False)
    pm_cid = data["pm_cid"]
    pm_mid = data["pm_mid"]

    if is_public:
        await state.set_state(UserTemplateStates.waiting_tags)
        await bot.edit_message_text(
            await t("tpl.send_tags", lang),
            chat_id=pm_cid,
            message_id=pm_mid,
            parse_mode="HTML",
            reply_markup=_cancel_kb(await t("common.cancel", lang)),
        )
        return

    title = _random_title()
    pool = get_pool()
    try:
        template = await add_user_template(
            pool, file_id, file_unique_id, file_type, title,
            submitted_by=user_id, is_public=False,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("add_user_template error: %s", e)
        await state.clear()
        await bot.edit_message_text(
            await t("tpl.add_error", lang),
            chat_id=pm_cid,
            message_id=pm_mid,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    await state.clear()

    if template is None:
        await bot.edit_message_text(
            await t("tpl.duplicate", lang),
            chat_id=pm_cid,
            message_id=pm_mid,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("tpl.add_another", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    await bot.edit_message_text(
        await t("tpl.added_personal", lang),
        chat_id=pm_cid,
        message_id=pm_mid,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("common.use_in_chat", lang), switch_inline_query="")],
            [InlineKeyboardButton(text=await t("common.add_more", lang), callback_data="template:add_user")],
            [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
        ]),
    )

    class _Stub:
        pass
    stub = _Stub()
    stub.bot = bot
    stub.from_user = _Stub()
    stub.from_user.id = user_id
    stub.from_user.username = None
    await _notify_admins(stub, template, personal=True)


@router.message(StateFilter(None), F.animation | F.photo | F.document)
async def handle_media_no_state(message: Message, state: FSMContext):
    media = _extract_media(message)
    if not media:
        return
    file_id, file_unique_id, file_type = media
    lang = await user_lang(message.from_user.id)

    sent = await message.answer(
        await t("tpl.add_choose_type", lang),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("tpl.add_quick", lang), callback_data="utpl:quick")],
            [InlineKeyboardButton(text=await t("tpl.add_public", lang), callback_data="utpl:public")],
            [InlineKeyboardButton(text=await t("common.cancel", lang), callback_data="utpl:cancel")],
        ]),
    )
    await state.set_state(UserTemplateStates.choosing_type)
    await state.update_data(
        pm_cid=sent.chat.id, pm_mid=sent.message_id,
        file_id=file_id, file_unique_id=file_unique_id, file_type=file_type,
    )


@router.callback_query(F.data == "utpl:cancel")
async def cb_cancel_add(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    msg = await get_bot_message(pool, "welcome", lang)
    btn_rows = await get_bot_buttons(pool, "main_menu", lang)
    kb = build_inline_kb(btn_rows) if btn_rows else main_menu()
    if msg.get("entities"):
        from aiogram.types import MessageEntity
        ents = [MessageEntity(**{k: v for k, v in e.items()}) for e in msg["entities"]]
        await callback.message.edit_text(msg["text"] or await t("common.menu", lang), entities=ents, parse_mode=None, reply_markup=kb)
    else:
        await callback.message.edit_text(msg["text"] or await t("common.menu", lang), parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(UserTemplateStates.waiting_media)
async def handle_user_media(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
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

    try:
        await message.delete()
    except Exception:
        pass

    if data["is_public"]:
        await state.update_data(file_id=file_id, file_unique_id=file_unique_id, file_type=file_type)
        await state.set_state(UserTemplateStates.waiting_tags)
        await message.bot.edit_message_text(
            await t("tpl.send_tags", lang),
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            parse_mode="HTML",
            reply_markup=_cancel_kb(await t("common.cancel", lang)),
        )
        return

    title = _random_title()
    pool = get_pool()
    try:
        template = await add_user_template(
            pool, file_id, file_unique_id, file_type, title,
            submitted_by=message.from_user.id, is_public=False,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("add_user_template error: %s", e)
        await message.bot.edit_message_text(
            await t("tpl.add_error", lang),
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    if template is None:
        await message.bot.edit_message_text(
            await t("tpl.duplicate", lang),
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("tpl.add_another", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    await message.bot.edit_message_text(
        await t("tpl.added_personal", lang),
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("common.use_in_chat", lang), switch_inline_query="")],
            [InlineKeyboardButton(text=await t("common.add_more", lang), callback_data="template:add_user")],
            [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
        ]),
    )
    await _notify_admins(message, template, personal=True)
    return


@router.message(UserTemplateStates.waiting_tags)
async def handle_user_tags(message: Message, state: FSMContext):
    import re
    lang = await user_lang(message.from_user.id)
    text = message.text or ""
    emoji_pattern = re.compile(
        "[\U0001F000-\U0001FFFF"
        "\U00002600-\U000027BF"
        "\U0001F300-\U0001F9FF"
        "\U0001FA00-\U0001FA9F"
        "\U0001FAA0-\U0001FAFF"
        "]+",
        flags=re.UNICODE,
    )
    tags = emoji_pattern.findall(text)
    split_tags = []
    for s in tags:
        for ch in s:
            if ch.strip():
                split_tags.append(ch)
    tags = list(dict.fromkeys(split_tags))[:5]

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()

    if not tags:
        await message.bot.edit_message_text(
            await t("tpl.tags_invalid", lang),
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            parse_mode="HTML",
            reply_markup=_cancel_kb(await t("common.cancel", lang)),
        )
        return

    title = _random_title()
    await state.clear()

    pool = get_pool()
    try:
        template = await add_user_template(
            pool, data["file_id"], data["file_unique_id"], data["file_type"], title,
            submitted_by=message.from_user.id, is_public=True,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("add_user_template error: %s", e)
        await message.bot.edit_message_text(
            await t("tpl.add_error", lang),
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    if template is None:
        await message.bot.edit_message_text(
            await t("tpl.duplicate", lang),
            chat_id=data["pm_cid"],
            message_id=data["pm_mid"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("tpl.add_another", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    from bot.database.queries import update_template_tags
    await update_template_tags(pool, template["id"], tags)

    await message.bot.edit_message_text(
        await t("tpl.sent_to_moderation", lang, tags="".join(tags)),
        chat_id=data["pm_cid"],
        message_id=data["pm_mid"],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
        ]),
    )
    template["tags"] = tags
    await _notify_admins(message, template, personal=False)


async def _notify_admins(message: Message, template: dict, personal: bool = False):
    import logging
    from bot.keyboards.admin import moderation_kb
    from datetime import datetime
    log = logging.getLogger(__name__)

    if personal:
        channel_id = settings.personal_channel_id
        if not channel_id:
            log.warning("PERSONAL_CHANNEL_ID not set — personal template %s won't be logged", template["id"])
            return
    else:
        channel_id = settings.moderation_channel_id
        if not channel_id:
            log.warning("MODERATION_CHANNEL_ID not set — template %s pending but admins won't see it", template["id"])
            return

    template_id = template["id"]
    user = message.from_user
    username = f"@{user.username}" if user.username else f"ID {user.id}"
    header = "🟢 Личный шаблон" if personal else "🔔 Новый шаблон на модерацию!"
    tags_line = ""
    if not personal:
        tpl_tags = template.get("tags") or []
        if tpl_tags:
            tags_line = f"\n🏷 Тэги: {' '.join(tpl_tags)}"
    caption = (
        f"{header}\n\n"
        f"👤 От: {username} (ID: <code>{user.id}</code>)\n"
        f"📋 Название: {template['title']}{tags_line}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    if personal:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"mod:delete:{template_id}"),
        ]])
    else:
        kb = moderation_kb(template_id)

    try:
        if template["file_type"] == "animation":
            await message.bot.send_animation(
                channel_id, template["file_id"],
                caption=caption, parse_mode="HTML", reply_markup=kb,
            )
        else:
            await message.bot.send_photo(
                channel_id, template["file_id"],
                caption=caption, parse_mode="HTML", reply_markup=kb,
            )
        log.info("template %s posted to channel %s (personal=%s)", template_id, channel_id, personal)
    except Exception as e:
        log.warning("failed to post template %s to channel %s: %s", template_id, channel_id, e)
