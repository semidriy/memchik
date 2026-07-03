import asyncio
import hashlib
import logging
import random
import time

import httpx
from aiogram import Bot, Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedMpeg4Gif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultPhoto,
    InputMediaAnimation,
    ChosenInlineResult,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_active_templates, get_templates_by_tags,
    record_inline_use, increment_template_use_count,
    get_eligible_placements_for_user, record_placement_send,
    get_cached_gif, get_cached_gif_batch, save_gif_cache, get_template, get_placement,
    is_user_bot_premium,
)
from bot.services.query_parser import parse_inline_query
from bot.services.inline_processor import build_preview_url, build_media_url, _sign, _text_hash
from bot.services.i18n import t, user_lang

router = Router()

_UPLOAD_SEM = asyncio.Semaphore(16)

# Держим ссылки на фоновые задачи заливки в кэш, иначе GC может убить их
# до завершения (asyncio.create_task не хранит ссылку сам).
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

# Telegram supplies chat_type only on InlineQuery, not on ChosenInlineResult.
# Cache user_id -> (chat_type, ts) at query time, look up at chosen time.
_CHAT_TYPE_CACHE: dict[int, tuple[str, float]] = {}
_CHAT_TYPE_TTL = 120.0


def _remember_chat_type(user_id: int, chat_type: str | None) -> None:
    if not chat_type:
        return
    _CHAT_TYPE_CACHE[user_id] = (chat_type, time.monotonic())
    if len(_CHAT_TYPE_CACHE) > 10000:
        now = time.monotonic()
        stale = [u for u, (_, ts) in _CHAT_TYPE_CACHE.items() if now - ts > _CHAT_TYPE_TTL]
        for u in stale:
            _CHAT_TYPE_CACHE.pop(u, None)


def _recall_chat_type(user_id: int) -> str | None:
    hit = _CHAT_TYPE_CACHE.get(user_id)
    if not hit:
        return None
    ct, ts = hit
    if time.monotonic() - ts > _CHAT_TYPE_TTL:
        _CHAT_TYPE_CACHE.pop(user_id, None)
        return None
    return ct

# Shared HTTP client — avoids per-request TLS handshake.
_HTTP: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.AsyncClient(timeout=20.0, http2=False,
                                  limits=httpx.Limits(max_keepalive_connections=20))
    return _HTTP


def _encode_result_id(template_id: int, overlay_text: str, placement_id: int | None) -> str:
    tid = str(template_id)
    pid = str(placement_id) if placement_id else ""
    if overlay_text:
        thash = hashlib.md5(overlay_text.encode()).hexdigest()[:8]
        return f"{tid}:{pid}:{thash}"
    return f"{tid}:{pid}" if pid else tid


def _placement_button(placement: dict | None) -> InlineKeyboardButton | None:
    if placement and placement.get("button_text") and placement.get("button_url"):
        return InlineKeyboardButton(text=placement["button_text"], url=placement["button_url"])
    return None


def _result_markup(placement: dict | None, next_label: str = "🔁 Ещё мем") -> InlineKeyboardMarkup:
    """Always returns a markup with at least one button. A reply_markup is required for
    Telegram to hand back an inline_message_id, which we need to swap the static preview
    for the animated gif after the user picks it."""
    btn = _placement_button(placement)
    if btn:
        return InlineKeyboardMarkup(inline_keyboard=[[btn]])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=next_label, switch_inline_query_current_chat=""),
    ]])


def _build_preview_result(template: dict, overlay_text: str, placement: dict | None, next_label: str = "🔁 Ещё мем"):
    """Inline result WITH overlay text — ВСЕГДА дешёвое статичное JPEG-превью (текст впечён).

    Почему не отдаём сразу анимацию через mpeg4_url: на каждую букву Телеграм при ответе
    тянет URL КАЖДОГО результата к себе. mpeg4_url запускает ffmpeg-рендер видео (~1-3 с
    на штуку) — медиасервер не успевает отдать 50 свежих видео в отведённое Телеграму
    окно, и тот выбрасывает все кроме 1-2. Поэтому в выдаче — статичные превью (PIL,
    ~10-50 мс), их Телеграм успевает забрать все → видно ВСЕ гифки. Для animation-шаблонов
    после выбора подменяем превью на живую гифку в handle_chosen_inline (там это ОДИН
    рендер выбранного, а не 50 на каждый ввод)."""
    title = template["title"] or "GIF"
    if overlay_text:
        title = f'"{overlay_text}" • {title}'
    caption = placement["caption_text"] if placement and placement.get("caption_text") else None
    rid = _encode_result_id(template["id"], overlay_text, placement["id"] if placement else None)
    url = build_preview_url(template["id"], overlay_text)
    # photo_width/photo_height обязательны — без них tdesktop часто вообще не
    # дёргает photo_url (bug telegramdesktop/tdesktop#4580). Точных размеров
    # не знаем, ставим разумные дефолты.
    return InlineQueryResultPhoto(
        id=rid, photo_url=url, thumbnail_url=url,
        photo_width=512, photo_height=512,
        title=title, caption=caption, reply_markup=_result_markup(placement, next_label),
    )


def _build_cached_gif_result(template: dict, file_id: str, overlay_text: str,
                             placement: dict | None, next_label: str = "🔁 Ещё мем"):
    """Готовая gif с текстом уже залита в кэш-канал → есть её telegram file_id.
    Отдаём как CachedMpeg4Gif: Телеграм тянет её со СВОЕГО CDN (медиасервер вообще
    не дёргается), в выдаче она грузится мгновенно, а при отправке улетает уже
    ГОТОВОЙ анимацией. Никакой правки сообщения после отправки не делаем →
    НЕТ пометки «изменено» (в этом вся суть, как у @memer)."""
    title = template["title"] or "GIF"
    if overlay_text:
        title = f'"{overlay_text}" • {title}'
    caption = placement["caption_text"] if placement and placement.get("caption_text") else None
    rid = _encode_result_id(template["id"], overlay_text, placement["id"] if placement else None)
    return InlineQueryResultCachedMpeg4Gif(
        id=rid, mpeg4_file_id=file_id,
        title=title, caption=caption, reply_markup=_result_markup(placement, next_label),
    )


def _build_plain_result(template: dict, placement: dict | None, next_label: str = "🔁 Ещё мем"):
    """No overlay text — show the original cached media directly (instant)."""
    title = template["title"] or "GIF"
    caption = placement["caption_text"] if placement and placement.get("caption_text") else None
    rid = _encode_result_id(template["id"], "", placement["id"] if placement else None)
    if template["file_type"] == "animation":
        return InlineQueryResultCachedMpeg4Gif(
            id=rid, mpeg4_file_id=template["file_id"],
            title=title, caption=caption, reply_markup=_result_markup(placement, next_label),
        )
    return InlineQueryResultCachedPhoto(
        id=rid, photo_file_id=template["file_id"],
        title=title, caption=caption, reply_markup=_result_markup(placement, next_label),
    )


async def _pick_placement(pool, user_id: int) -> dict | None:
    eligible = await get_eligible_placements_for_user(pool, user_id)
    return random.choice(eligible) if eligible else None


async def _upload_one(bot, pool, template: dict, text: str, text_hash: str) -> tuple[str, str | None]:
    """Ask media server to process the animation + upload to cache_chat. Returns the file_id."""
    gif_id = str(template["id"])
    async with _UPLOAD_SEM:
        try:
            base = settings.media_server_url.rstrip("/")
            sig = _sign(template["id"], text)
            url = f"{base}/cache/{template['id']}"
            params = {"text": text, "sig": sig} if sig else {"text": text}
            resp = await _get_http().post(url, params=params, timeout=30.0)
            if resp.status_code != 200:
                logger.warning("media server /cache %d for template %s: %s",
                               resp.status_code, gif_id, resp.text[:200])
                return gif_id, None
            file_id = resp.json().get("file_id")
            if not file_id:
                return gif_id, None
            await save_gif_cache(pool, gif_id, text_hash, file_id)
            logger.info("uploaded template_id=%s text_hash=%s", gif_id, text_hash)
            return gif_id, file_id
        except Exception as e:
            logger.warning("upload failed template_id=%s: %s", gif_id, e)
            return gif_id, None


@router.inline_query()
async def handle_inline_query(query: InlineQuery):
    pool = get_pool()
    user_id = query.from_user.id
    _remember_chat_type(user_id, query.chat_type)
    emoji_tags, overlay_text = parse_inline_query(query.query)

    # Pagination — Telegram hands back the next_offset we returned as the user scrolls.
    # Empty offset = first page. We serve up to PAGE_SIZE (TG's hard cap is 50 per answer)
    # and keep returning a next_offset until a short page signals the end. This lifts the
    # old 20-result ceiling so the whole library is reachable by scrolling.
    try:
        offset = int(query.offset) if query.offset else 0
    except ValueError:
        offset = 0
    # Без текста результат — это готовый cached file_id (мгновенно), поэтому отдаём 50.
    # С текстом КАЖДЫЙ результат = свежий рендер превью на медиасервере (/preview), и на
    # холодную ему нужно скачать исходник с TG + вытащить кадр ffmpeg перед дешёвым PIL-
    # композитом. 50 таких рендеров на КАЖДУЮ букву медиасервер не успевает отдать в узкое
    # окно Телеграма → часть превью приходит «белыми / через раз». Меньшая первая страница
    # для текстовых запросов = все превью успевают прогрузиться; остальное подгружается при
    # скролле (next_offset уже работает).
    PAGE_SIZE = 12 if overlay_text else 50

    if emoji_tags:
        templates = await get_templates_by_tags(pool, emoji_tags, limit=PAGE_SIZE, user_id=user_id, offset=offset)
    else:
        templates = await get_active_templates(pool, limit=PAGE_SIZE, user_id=user_id, offset=offset)

    lang = await user_lang(user_id)
    if not templates:
        if offset > 0:
            # Scrolled past the last page — stop quietly, don't re-show the PM prompt.
            await query.answer(results=[], cache_time=10, is_personal=True)
            return
        await query.answer(
            results=[],
            cache_time=30,
            switch_pm_text=await t("inline.no_templates", lang),
            switch_pm_parameter="start",
        )
        return

    has_bot_premium = await is_user_bot_premium(pool, user_id)
    placement = None if has_bot_premium else await _pick_placement(pool, user_id)
    next_label = await t("inline.next_meme", lang)

    results = []
    if overlay_text:
        # Cache-first: для (шаблон, текст), которые УЖЕ залиты в кэш-канал, отдаём
        # готовую анимацию по file_id (мгновенно, без правки → без «изменено»).
        # Остальные пока показываем дешёвым статичным превью — они дозальются в
        # кэш при выборе (см. handle_chosen_inline) и в следующий раз тоже станут
        # мгновенными gif. Так выдача сходится к поведению @memer.
        text_hash = _text_hash(overlay_text)
        anim_ids = [str(t["id"]) for t in templates if t["file_type"] == "animation"]
        cached_map = await get_cached_gif_batch(pool, anim_ids, text_hash) if anim_ids else {}
        for tpl in templates:
            cached_fid = cached_map.get(str(tpl["id"])) if tpl["file_type"] == "animation" else None
            if cached_fid:
                results.append(_build_cached_gif_result(tpl, cached_fid, overlay_text, placement, next_label))
            else:
                results.append(_build_preview_result(tpl, overlay_text, placement, next_label))
    else:
        for tpl in templates:
            results.append(_build_plain_result(tpl, placement, next_label))

    # While typing overlay text, force fresh results (cache_time=0) so the preview never
    # lags a keystroke behind — e.g. adding/removing "?" updates immediately instead of
    # Telegram serving the previous cached render.
    cache_time = 0 if overlay_text else 10
    # Full page → there may be more; hand Telegram an offset to fetch the next page.
    next_offset = str(offset + len(templates)) if len(templates) == PAGE_SIZE else ""
    logger.info("inline uid=%d query=%r tpls=%d with_text=%s offset=%d next=%r",
                user_id, query.query, len(templates), bool(overlay_text), offset, next_offset)

    try:
        await query.answer(results=results, cache_time=cache_time, is_personal=True,
                           next_offset=next_offset)
    except TelegramBadRequest as e:
        logger.warning("inline answer failed: %s", e)
        await query.answer(results=[], cache_time=10)


@router.chosen_inline_result()
async def handle_chosen_inline(result: ChosenInlineResult, bot: Bot):
    pool = get_pool()
    raw = result.result_id or ""
    parts = raw.split(":")
    template_id_str = parts[0] if parts else ""
    placement_id_str = parts[1] if len(parts) >= 2 else ""

    template_id = None
    try:
        template_id = int(template_id_str)
        await increment_template_use_count(pool, template_id)
    except ValueError:
        pass

    placement_id = None
    if placement_id_str:
        try:
            placement_id = int(placement_id_str)
            await record_placement_send(pool, placement_id, result.from_user.id, None)
        except ValueError:
            pass

    chat_type = _recall_chat_type(result.from_user.id) or "inline"
    await record_inline_use(
        pool,
        user_id=result.from_user.id,
        chat_id=0,
        chat_type=chat_type,
        query=result.query,
        gif_id=str(template_id) if template_id is not None else raw,
    )

    if template_id is None:
        return
    _, overlay_text = parse_inline_query(result.query or "")
    if not overlay_text:
        # Без текста результат и так был готовой анимацией (cached file_id) — ничего не делаем.
        return
    try:
        template = await get_template(pool, template_id)
        if not template or template.get("file_type") != "animation":
            return
        text_hash = _text_hash(overlay_text)
        cached = await get_cached_gif(pool, str(template_id), text_hash)
        if cached:
            # Этот (шаблон, текст) уже в кэше → в выдаче он был отдан ГОТОВОЙ gif
            # (CachedMpeg4Gif). Улетело сразу анимацией, править нечего — а значит
            # НЕТ пометки «изменено». Это и есть целевое поведение.
            return

        # Не было в кэше → в выдаче показали статичное превью. Заливаем эту gif в
        # кэш-канал В ФОНЕ, чтобы СЛЕДУЮЩИЙ раз этот же текст отдавался мгновенной
        # gif без правки и без «изменено». А текущее сообщение разово подменяем на
        # анимацию (только здесь, на самом первом использовании текста, мелькнёт
        # «изменено» — до gif ещё физически не существует, деться от этого некуда).
        _spawn_bg(_upload_one(bot, pool, template, overlay_text, text_hash))

        if not result.inline_message_id:
            return
        placement = await get_placement(pool, placement_id) if placement_id else None
        caption = placement["caption_text"] if placement and placement.get("caption_text") else None
        lang = await user_lang(result.from_user.id)
        next_label = await t("inline.next_meme", lang)
        await bot.edit_message_media(
            media=InputMediaAnimation(
                media=build_media_url(template_id, overlay_text, "mp4"), caption=caption,
            ),
            inline_message_id=result.inline_message_id,
            reply_markup=_result_markup(placement, next_label),
        )
    except Exception as e:
        logger.warning("inline animation swap failed tid=%s: %s", template_id, e)
