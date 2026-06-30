import asyncio
import io
import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_active_templates, get_templates_count, get_template,
    get_cached_gif, save_gif_cache, increment_template_use_count,
)
from bot.keyboards.user import templates_page
from bot.services.overlay import add_text_auto
from bot.services.detector import hash_text
from bot.services.i18n import t, user_lang
from bot.states.admin import MemeStates

logger = logging.getLogger(__name__)
router = Router()
PER_PAGE = 8


async def _show_templates(target, page: int = 0, user_id: int | None = None):
    pool = get_pool()
    templates = await get_active_templates(pool, limit=PER_PAGE, offset=page * PER_PAGE, user_id=user_id)
    total = await get_templates_count(pool, active_only=True, user_id=user_id)
    lang = await user_lang(user_id) if user_id else "ru"

    if not templates:
        text = await t("meme.none", lang)
        if hasattr(target, "message"):
            await target.message.answer(text)
        else:
            await target.answer(text)
        return False

    markup = templates_page(templates, page, total, PER_PAGE)
    choose = await t("meme.choose", lang)
    if hasattr(target, "message"):
        await target.message.answer(choose, reply_markup=markup)
    else:
        await target.answer(choose, reply_markup=markup)
    return True


@router.callback_query(F.data == "meme:start")
async def cb_meme_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MemeStates.waiting_template)
    await _show_templates(callback, user_id=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("meme:page:"))
async def cb_meme_page(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":")[2])
    pool = get_pool()
    user_id = callback.from_user.id
    templates = await get_active_templates(pool, limit=PER_PAGE, offset=page * PER_PAGE, user_id=user_id)
    total = await get_templates_count(pool, active_only=True, user_id=user_id)
    await callback.message.edit_reply_markup(
        reply_markup=templates_page(templates, page, total, PER_PAGE)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meme:pick:"), MemeStates.waiting_template)
async def cb_meme_pick(callback: CallbackQuery, state: FSMContext):
    template_id = int(callback.data.split(":")[2])
    pool = get_pool()
    tpl = await get_template(pool, template_id)
    lang = await user_lang(callback.from_user.id)
    if not tpl or not tpl["is_active"]:
        await callback.answer(await t("meme.tpl_unavailable", lang), show_alert=True)
        return

    await state.set_state(MemeStates.waiting_text)
    await state.update_data(
        template_id=template_id,
        file_id=tpl["file_id"],
        file_type=tpl["file_type"],
    )

    if tpl["file_type"] == "animation":
        await callback.message.answer_animation(tpl["file_id"])
    else:
        await callback.message.answer_photo(tpl["file_id"])

    await callback.message.answer(await t("meme.send_text", lang))
    await callback.answer()


@router.message(MemeStates.waiting_text)
async def handle_meme_text(message: Message, state: FSMContext):
    data = await state.get_data()
    template_id = data.get("template_id")
    file_id = data.get("file_id")
    file_type = data.get("file_type")

    lang = await user_lang(message.from_user.id)
    if not file_id:
        await state.clear()
        await message.answer(await t("meme.restart", lang))
        return

    text = message.text.strip()
    if len(text) > 100:
        await message.answer(await t("meme.too_long", lang))
        return

    pool = get_pool()
    text_hash = hash_text(text)
    cached_file_id = await get_cached_gif(pool, str(template_id), text_hash)

    if cached_file_id:
        if file_type == "animation":
            await message.answer_animation(cached_file_id, caption="🎭")
        else:
            await message.answer_photo(cached_file_id, caption="🎭")
        if template_id:
            await increment_template_use_count(pool, template_id)
        await state.clear()
        return

    processing_msg = await message.answer(await t("meme.processing", lang))

    try:
        buf = io.BytesIO()
        await message.bot.download(file_id, destination=buf)
        original_bytes = buf.getvalue()

        processed, is_video = await asyncio.get_event_loop().run_in_executor(
            None, add_text_auto, original_bytes, text
        )
        upload_type = "animation" if is_video else "photo"
        result_file_id = await _upload_and_cache(
            message, pool, processed, upload_type, template_id, text_hash
        )
        if upload_type == "animation":
            await message.answer_animation(result_file_id, caption="🎭")
        else:
            await message.answer_photo(result_file_id, caption="🎭")

        if template_id:
            await increment_template_use_count(pool, template_id)

    except Exception as e:
        logger.error("Meme processing failed for template %s: %s", template_id, e, exc_info=True)
        await message.answer(await t("meme.error", lang, err=type(e).__name__), parse_mode=None)
    finally:
        await processing_msg.delete()
        await state.clear()


async def _upload_and_cache(message: Message, pool, data: bytes, file_type: str, template_id, text_hash: str) -> str:
    try:
        if file_type == "animation":
            sent = await message.bot.send_animation(
                settings.cache_chat_id,
                BufferedInputFile(data, filename="meme.gif"),
            )
            file_id = sent.animation.file_id
        else:
            sent = await message.bot.send_photo(
                settings.cache_chat_id,
                BufferedInputFile(data, filename="meme.jpg"),
            )
            file_id = sent.photo[-1].file_id

        await save_gif_cache(pool, str(template_id), text_hash, file_id)
        return file_id

    except Exception as cache_err:
        logger.warning("Cache chat upload failed (%s), sending directly to user", cache_err)
        if file_type == "animation":
            sent = await message.answer_animation(BufferedInputFile(data, filename="meme.gif"))
            return sent.animation.file_id
        else:
            sent = await message.answer_photo(BufferedInputFile(data, filename="meme.jpg"))
            return sent.photo[-1].file_id
