import hashlib
import logging
import random
import string

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, Message, MessageEntity,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaAnimation, InputMediaVideo, InputMediaPhoto,
)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import add_user_template, get_bot_message, get_bot_buttons
from bot.handlers.user_nav import _edit_with_msg
from bot.keyboards.user import build_inline_kb, main_menu
from bot.services.i18n import t, user_lang
from bot.services.media_intake import video_to_animation
from bot.services.query_parser import extract_emojis
from bot.states.admin import UserTemplateStates


def _random_title() -> str:
    return "Шаблон-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


async def _content_hash(bot, file_id: str) -> str | None:
    """sha256 сырых байт файла — для дедупа публичных шаблонов между разными юзерами.
    Падение скачивания не должно ломать добавление: тогда вернём None и дедуп откатится
    на проверку по file_unique_id."""
    try:
        f = await bot.get_file(file_id)
        buf = await bot.download_file(f.file_path)
        data = buf.read() if hasattr(buf, "read") else buf
        return hashlib.sha256(data).hexdigest()
    except Exception as e:
        logging.getLogger(__name__).warning("content_hash failed for %s: %s", file_id, e)
        return None

router = Router()
logger = logging.getLogger(__name__)


def _cancel_kb(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="utpl:cancel")],
    ])


async def _choose_type_kb(lang: str) -> InlineKeyboardMarkup:
    # Кнопки чузера живут в bot_buttons (key=tpl_choose_menu): каждую можно менять
    # отдельно — текст, цвет, прем-иконку — через админку «Кнопки», как у main_menu.
    rows = await get_bot_buttons(get_pool(), "tpl_choose_menu", lang)
    if rows:
        return build_inline_kb(rows)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=await t("tpl.add_quick", lang), callback_data="utpl:quick")],
        [InlineKeyboardButton(text=await t("tpl.add_public", lang), callback_data="utpl:public")],
        [InlineKeyboardButton(text=await t("common.cancel", lang), callback_data="utpl:cancel")],
    ])


async def _replace_panel(bot, chat_id: int, old_mid: int, text: str, kb,
                         parse_mode: str = "HTML", entities=None, state: FSMContext = None):
    """Показать следующий ТЕКСТОВЫЙ шаг панели. Сначала пробуем отредактировать старое
    сообщение на месте (переход без «мигания», как в мемере). Если панель была
    медиа-сообщением (у chooser'а гифка из менюшки) — текстом её не отредактировать,
    тогда пересоздаём: удаляем и шлём новое. Если передан state — обновляем в нём
    pm_cid/pm_mid (следующий хендлер работает с этим же сообщением)."""
    kw = {"reply_markup": kb}
    if entities:
        kw["entities"] = entities
    else:
        kw["parse_mode"] = parse_mode
    if old_mid:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=old_mid, **kw)
            if state is not None:
                await state.update_data(pm_cid=chat_id, pm_mid=old_mid)
            return None
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                if state is not None:
                    await state.update_data(pm_cid=chat_id, pm_mid=old_mid)
                return None
        except Exception:
            pass
        try:
            await bot.delete_message(chat_id, old_mid)
        except Exception:
            pass
    sent = await bot.send_message(chat_id, text, **kw)
    if state is not None:
        await state.update_data(pm_cid=sent.chat.id, pm_mid=sent.message_id)
    return sent


async def _send_chooser(message: Message, lang: str, edit_from: Message | None = None) -> Message:
    """Экран «Каким способом добавить шаблон?». Если к нему в админке прикреплено медиа
    (видео/гиф/фото) — шлём ОДНИМ сообщением: медиа + подпись(text) + кнопки. Возвращаем
    это сообщение (его id кладём в pm_mid — на нём строится дальнейший флоу).

    edit_from — прежняя менюшка: если её тип совпадает с чузером (медиа↔медиа или
    текст↔текст), редактируем НА МЕСТЕ — переход плавный, без удаления/пересоздания."""
    pool = get_pool()
    msg = await get_bot_message(pool, "tpl.add_choose_type", lang)
    kb = await _choose_type_kb(lang)
    text = msg["text"] or await t("tpl.add_choose_type", lang)
    ents = None
    if msg.get("entities"):
        ents = [MessageEntity(**{k: v for k, v in e.items()}) for e in msg["entities"]]
    fid, ftype = msg.get("file_id"), msg.get("file_type")
    # Диагностика: что бот ПРОЧИТАЛ из БД для этой менюшки (текст + энтити/прем-эмодзи).
    logger.info(
        "CHOOSER-READ lang=%s text_len=%d media=%s entities=%s",
        lang, len(text), ftype,
        [(e.type, e.offset, e.length, e.custom_emoji_id) for e in (ents or [])],
    )
    if edit_from is not None:
        cur_is_media = edit_from.content_type in ("animation", "video", "photo")
        can_edit = (bool(fid) and cur_is_media) or (not fid and not cur_is_media)
        if can_edit:
            for attempt_ents in ([ents, None] if ents else [None]):
                try:
                    if fid:
                        ikw = {"media": fid, "caption": text or None}
                        if attempt_ents:
                            ikw["caption_entities"] = attempt_ents
                            ikw["parse_mode"] = None
                        else:
                            ikw["parse_mode"] = "HTML"
                        cls = (InputMediaVideo if ftype == "video"
                               else InputMediaPhoto if ftype == "photo"
                               else InputMediaAnimation)
                        edited = await edit_from.edit_media(cls(**ikw), reply_markup=kb)
                    elif attempt_ents:
                        edited = await edit_from.edit_text(text, entities=attempt_ents, parse_mode=None, reply_markup=kb)
                    else:
                        edited = await edit_from.edit_text(text, parse_mode="HTML", reply_markup=kb)
                    return edited if isinstance(edited, Message) else edit_from
                except TelegramBadRequest as e:
                    if "message is not modified" in str(e):
                        return edit_from
                    logger.warning("CHOOSER in-place edit failed (ents=%s): %s", bool(attempt_ents), e)
                except Exception as e:
                    logger.warning("CHOOSER in-place edit failed (ents=%s): %s", bool(attempt_ents), e)
        # Тип сообщения не совпал или редактирование не прошло — пересоздаём.
        try:
            await edit_from.delete()
        except Exception:
            pass
    if fid:
        # Шлём медиа+подпись+кнопки одним сообщением; если Телеграм отвергнет энтити
        # (напр. недоступный прем-эмодзи) — повторяем БЕЗ энтитей, чтобы экран не падал.
        for attempt_ents in ([ents, None] if ents else [None]):
            kw = {"caption": text or None, "reply_markup": kb}
            if attempt_ents:
                # parse_mode=None ОБЯЗАТЕЛЕН: у бота дефолт parse_mode=HTML, и без явного
                # сброса Телеграм парсит подпись как HTML и ИГНОРИРУЕТ caption_entities
                # (из-за этого пропадали и формат, и прем-эмодзи).
                kw["caption_entities"] = attempt_ents
                kw["parse_mode"] = None
            else:
                kw["parse_mode"] = "HTML"
            try:
                if ftype == "video":
                    return await message.answer_video(fid, **kw)
                if ftype == "photo":
                    return await message.answer_photo(fid, **kw)
                return await message.answer_animation(fid, **kw)
            except Exception as e:
                logger.warning("CHOOSER media send failed (ents=%s): %s", bool(attempt_ents), e)
    for attempt_ents in ([ents, None] if ents else [None]):
        try:
            if attempt_ents:
                return await message.answer(text, entities=attempt_ents, parse_mode=None, reply_markup=kb)
            return await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.warning("CHOOSER text send failed (ents=%s): %s", bool(attempt_ents), e)
    return await message.answer(text, parse_mode=None, reply_markup=kb)


@router.callback_query(F.data == "template:add_user")
async def cb_add_user_template(callback: CallbackQuery, state: FSMContext):
    lang = await user_lang(callback.from_user.id)
    await state.set_state(UserTemplateStates.choosing_type)
    # Меню → чузер: редактируем на месте, когда типы совпадают (медиа↔медиа /
    # текст↔текст) — переход плавный; иначе _send_chooser сам пересоздаст.
    sent = await _send_chooser(callback.message, lang, edit_from=callback.message)
    await state.update_data(pm_cid=sent.chat.id, pm_mid=sent.message_id)
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
    await _replace_panel(
        callback.bot, callback.message.chat.id, callback.message.message_id,
        await t("tpl.send_media", lang),
        _cancel_kb(await t("common.cancel", lang)),
        state=state,
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


async def _extract_media_async(message: Message):
    """Как _extract_media, но дополнительно принимает message.video (обычный MP4 из
    галереи): перезаливает его как animation. Старый код видео не принимал → «видео
    не добавляется»."""
    if message.video:
        try:
            fid, fuid = await video_to_animation(message.bot, message.video.file_id)
            return fid, fuid, "animation"
        except Exception as e:
            logging.getLogger(__name__).warning("video->animation failed: %s", e)
            return message.video.file_id, message.video.file_unique_id, "animation"
    return _extract_media(message)


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
        await _replace_panel(
            bot, pm_cid, pm_mid,
            await t("tpl.send_tags", lang),
            _cancel_kb(await t("common.cancel", lang)),
            state=state,
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
        await _replace_panel(
            bot, pm_cid, pm_mid,
            await t("tpl.add_error", lang),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    await state.clear()

    if template is None:
        await _replace_panel(
            bot, pm_cid, pm_mid,
            await t("tpl.duplicate", lang),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("tpl.add_another", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    await _replace_panel(
        bot, pm_cid, pm_mid,
        await t("tpl.added_personal", lang),
        InlineKeyboardMarkup(inline_keyboard=[
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


@router.message(StateFilter(None), F.animation | F.photo | F.document | F.video)
async def handle_media_no_state(message: Message, state: FSMContext):
    media = await _extract_media_async(message)
    if not media:
        return
    file_id, file_unique_id, file_type = media
    lang = await user_lang(message.from_user.id)

    sent = await _send_chooser(message, lang)
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
    # Чузер → главное меню: _edit_with_msg редактирует на месте, когда типы совпадают
    # (медиа↔медиа / текст↔текст), и пересоздаёт только когда иначе нельзя.
    await _edit_with_msg(callback, msg, await t("common.menu", lang), kb)
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
    elif message.video:
        try:
            file_id, file_unique_id = await video_to_animation(message.bot, message.video.file_id)
            file_type = "animation"
        except Exception as e:
            logging.getLogger(__name__).warning("video->animation failed: %s", e)
            file_id, file_unique_id, file_type = (
                message.video.file_id, message.video.file_unique_id, "animation"
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
        await _replace_panel(
            message.bot, data["pm_cid"], data["pm_mid"],
            await t("tpl.send_tags", lang),
            _cancel_kb(await t("common.cancel", lang)),
            state=state,
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
        await _replace_panel(
            message.bot, data["pm_cid"], data["pm_mid"],
            await t("tpl.add_error", lang),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    if template is None:
        await _replace_panel(
            message.bot, data["pm_cid"], data["pm_mid"],
            await t("tpl.duplicate", lang),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("tpl.add_another", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    await _replace_panel(
        message.bot, data["pm_cid"], data["pm_mid"],
        await t("tpl.added_personal", lang),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("common.use_in_chat", lang), switch_inline_query="")],
            [InlineKeyboardButton(text=await t("common.add_more", lang), callback_data="template:add_user")],
            [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
        ]),
    )
    await _notify_admins(message, template, personal=True)
    return


@router.message(UserTemplateStates.waiting_tags)
async def handle_user_tags(message: Message, state: FSMContext):
    lang = await user_lang(message.from_user.id)
    # Та же нормализация, что у админских тегов и у инлайн-поиска (см. query_parser) —
    # иначе сохранённый тег байтово не совпадает с эмодзи из поиска и ничего не находится.
    tags = extract_emojis(message.text or "")[:5]

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()

    if not tags:
        await _replace_panel(
            message.bot, data["pm_cid"], data["pm_mid"],
            await t("tpl.tags_invalid", lang),
            _cancel_kb(await t("common.cancel", lang)),
            state=state,
        )
        return

    title = _random_title()
    await state.clear()

    pool = get_pool()
    chash = await _content_hash(message.bot, data["file_id"])
    try:
        template = await add_user_template(
            pool, data["file_id"], data["file_unique_id"], data["file_type"], title,
            submitted_by=message.from_user.id, is_public=True, content_hash=chash,
        )
    except Exception as e:
        logging.getLogger(__name__).error("add_user_template error: %s", e)
        await _replace_panel(
            message.bot, data["pm_cid"], data["pm_mid"],
            await t("tpl.add_error", lang),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    if template is None:
        await _replace_panel(
            message.bot, data["pm_cid"], data["pm_mid"],
            await t("tpl.duplicate", lang),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=await t("tpl.add_another", lang), callback_data="template:add_user")],
                [InlineKeyboardButton(text=await t("common.go_menu", lang), callback_data="utpl:cancel")],
            ]),
        )
        return

    from bot.database.queries import update_template_tags
    await update_template_tags(pool, template["id"], tags)

    await _replace_panel(
        message.bot, data["pm_cid"], data["pm_mid"],
        await t("tpl.sent_to_moderation", lang, tags="".join(tags)),
        InlineKeyboardMarkup(inline_keyboard=[
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
