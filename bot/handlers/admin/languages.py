"""Admin section: manage UI languages and per-language translations.

Translation keys come from two sources:
 - i18n_strings table (hardcoded UI strings; key list lives in i18n_defaults.DEFAULTS)
 - bot_messages table (welcome / op_required / op_passed / meme_send_text — editable long texts)

Buttons in bot_buttons are *not* translated here. They are admin-curated via the Buttons
section; if you need a localized button row, add it via Buttons with the target language set.
"""
import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

from bot.database import get_pool
from bot.database.queries import (
    get_languages, get_language, add_language, update_language, delete_language,
    get_i18n_string, set_i18n_string,
    get_bot_message, set_bot_message, set_bot_message_media,
    get_all_bot_buttons, get_bot_buttons, set_bot_buttons,
)
from bot.keyboards.admin import (
    languages_menu, languages_list, language_item, languages_cancel_kb,
)
from bot.services.permissions import has_permission, PERM_LANGUAGES
from bot.services.i18n_defaults import DEFAULTS
from bot.states.admin import LanguageStates

router = Router()


# Keys that belong to bot_messages (long texts), not i18n_strings.
# settings/premium включены, чтобы тексты этих менюшек (с прем-эмодзи) тоже
# редактировались по каждому языку, а не только главное меню.
_BOT_MESSAGE_KEYS = ["welcome", "op_required", "op_passed", "meme_send_text", "settings", "premium", "tpl.add_choose_type", "tpl.send_media_quick", "tpl.send_media_public"]
_PAGE_SIZE = 10


def _all_translation_keys() -> list[str]:
    """All keys an admin needs to translate, in the order they're shown."""
    return _BOT_MESSAGE_KEYS + sorted(DEFAULTS.keys())


async def _check(callback: CallbackQuery) -> bool:
    if not await has_permission(callback.from_user.id, PERM_LANGUAGES):
        await callback.answer("Нет доступа", show_alert=True)
        return False
    return True


@router.callback_query(F.data == "admin:languages")
async def cb_admin_languages(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    await state.clear()
    await callback.message.edit_text("🌐 Языки", reply_markup=languages_menu())
    await callback.answer()


@router.callback_query(F.data == "lang:list")
async def cb_lang_list(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    await state.clear()
    pool = get_pool()
    langs = await get_languages(pool)
    if not langs:
        await callback.message.edit_text("Языков пока нет.", reply_markup=languages_menu())
    else:
        await callback.message.edit_text("🌐 Языки:", reply_markup=languages_list(langs))
    await callback.answer()


@router.callback_query(F.data.startswith("lang:view:"))
async def cb_lang_view(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    await state.clear()
    code = callback.data.split(":", 2)[2]
    pool = get_pool()
    L = await get_language(pool, code)
    if not L:
        await callback.answer("Язык не найден", show_alert=True)
        return
    status = "🟢 включён" if L["is_active"] else "🔴 выключен"
    await callback.message.edit_text(
        f"{L['flag_emoji']} <b>{L['name']}</b>\n"
        f"Код: <code>{L['code']}</code>\n"
        f"Статус: {status}",
        parse_mode="HTML",
        reply_markup=language_item(code, L["is_active"]),
    )
    await callback.answer()


@router.callback_query(F.data == "lang:new")
async def cb_lang_new(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    await state.set_state(LanguageStates.waiting_new_input)
    await state.update_data(pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id)
    await callback.message.edit_text(
        "➕ <b>Добавить язык</b>\n\n"
        "Отправь одной строкой:\n"
        "<code>код|название|флаг</code>\n\n"
        "Пример: <code>uk|Українська|🇺🇦</code>",
        parse_mode="HTML",
        reply_markup=languages_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "lang:cancel")
async def cb_lang_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🌐 Языки", reply_markup=languages_menu())
    await callback.answer()


@router.message(LanguageStates.waiting_new_input)
async def handle_new_lang(message: Message, state: FSMContext):
    if not await has_permission(message.from_user.id, PERM_LANGUAGES):
        return
    text = (message.text or "").strip()
    parts = [p.strip() for p in text.split("|")]
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass

    if len(parts) < 2:
        await message.bot.edit_message_text(
            "⚠️ Неверный формат. Ожидаю <code>код|название|флаг</code>.",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML", reply_markup=languages_cancel_kb(),
        )
        return
    code, name = parts[0].lower(), parts[1]
    flag = parts[2] if len(parts) > 2 else "🌐"
    if not code or len(code) > 5:
        await message.bot.edit_message_text(
            "⚠️ Код языка должен быть до 5 символов.",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            reply_markup=languages_cancel_kb(),
        )
        return

    pool = get_pool()
    L = await add_language(pool, code, name, flag)
    await state.clear()
    if not L:
        await message.bot.edit_message_text(
            f"⚠️ Не удалось добавить язык <code>{code}</code> (возможно, уже существует).",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML", reply_markup=languages_menu(),
        )
        return
    await message.bot.edit_message_text(
        f"✅ Язык <b>{L['name']}</b> {L['flag_emoji']} добавлен.\n"
        "Не забудь заполнить переводы.",
        chat_id=data["pm_cid"], message_id=data["pm_mid"],
        parse_mode="HTML", reply_markup=language_item(code, L["is_active"]),
    )


@router.callback_query(F.data.startswith("lang:toggle:"))
async def cb_lang_toggle(callback: CallbackQuery):
    if not await _check(callback):
        return
    code = callback.data.split(":", 2)[2]
    if code == "ru":
        await callback.answer("Базовый язык нельзя выключить", show_alert=True)
        return
    pool = get_pool()
    L = await get_language(pool, code)
    if not L:
        await callback.answer("Не найден", show_alert=True)
        return
    await update_language(pool, code, is_active=not L["is_active"])
    L2 = await get_language(pool, code)
    status = "🟢 включён" if L2["is_active"] else "🔴 выключен"
    await callback.message.edit_text(
        f"{L2['flag_emoji']} <b>{L2['name']}</b>\n"
        f"Код: <code>{L2['code']}</code>\n"
        f"Статус: {status}",
        parse_mode="HTML",
        reply_markup=language_item(code, L2["is_active"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:delete:"))
async def cb_lang_delete(callback: CallbackQuery):
    if not await _check(callback):
        return
    code = callback.data.split(":", 2)[2]
    if code == "ru":
        await callback.answer("Базовый язык нельзя удалить", show_alert=True)
        return
    pool = get_pool()
    await delete_language(pool, code)
    langs = await get_languages(pool)
    await callback.message.edit_text(
        f"🗑 Язык <code>{code}</code> удалён.",
        parse_mode="HTML",
        reply_markup=languages_list(langs) if langs else languages_menu(),
    )
    await callback.answer()


def _key_hint(k: str) -> str:
    """Short RU preview so admin recognises what the key is about."""
    from bot.services.i18n_defaults import DEFAULTS
    val = DEFAULTS.get(k, "")
    if not val:
        # bot_messages keys — just show a generic label
        labels = {
            "welcome": "Приветствие при /start",
            "op_required": "Текст требования ОП",
            "op_passed": "Текст после прохождения ОП",
            "meme_send_text": "Текст «введи текст для мема»",
            "settings": "Заголовок меню настроек",
            "premium": "Текст меню Premium",
            "tpl.add_choose_type": "Экран «как добавить шаблон»",
            "tpl.send_media_quick": "Текст после «Быстро добавить»",
            "tpl.send_media_public": "Текст после «Публичный шаблон»",
        }
        return labels.get(k, "")
    short = val.replace("\n", " ").replace("<b>", "").replace("</b>", "")
    return short[:35] + ("…" if len(short) > 35 else "")


def _tr_keys_page_kb(code: str, page: int) -> InlineKeyboardMarkup:
    keys = _all_translation_keys()
    total = len(keys)
    start = page * _PAGE_SIZE
    end = min(start + _PAGE_SIZE, total)
    rows = []
    for k in keys[start:end]:
        hint = _key_hint(k)
        label = f"{k}" if not hint else f"{k} — {hint}"
        if len(label) > 60:
            label = label[:58] + "…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"lang:edit:{code}:{page}:{k}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"lang:tr:{code}:{page-1}"))
    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="lang:noop"))
    if end < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"lang:tr:{code}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ К языку", callback_data=f"lang:view:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("lang:tr:"))
async def cb_lang_translations(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    await state.clear()
    _, _, code, page_str = callback.data.split(":", 3)
    try:
        page = int(page_str)
    except ValueError:
        page = 0
    await callback.message.edit_text(
        f"✏️ <b>Переводы [{code}]</b>\n"
        "Выбери ключ для редактирования. Пустой = используется RU-фолбэк.",
        parse_mode="HTML",
        reply_markup=_tr_keys_page_kb(code, page),
    )
    await callback.answer()


async def _get_current_translation(pool, code: str, key: str) -> tuple[str, str]:
    """Returns (current_value_for_code, ru_reference)."""
    if key in _BOT_MESSAGE_KEYS:
        cur = await get_bot_message(pool, key, code)
        ru = await get_bot_message(pool, key, "ru")
        return cur["text"], ru["text"]
    cur = await get_i18n_string(pool, key, code)
    ru = await get_i18n_string(pool, key, "ru") or DEFAULTS.get(key, "")
    return (cur if cur is not None else ""), (ru or "")


@router.callback_query(F.data.startswith("lang:edit:"))
async def cb_lang_edit_key(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    # format: lang:edit:{code}:{page}:{key}
    parts = callback.data.split(":", 4)
    code = parts[2]
    try:
        page = int(parts[3])
        key = parts[4]
    except (IndexError, ValueError):
        # legacy format without page
        page = 0
        key = parts[3] if len(parts) > 3 else ""
    pool = get_pool()
    cur, ru = await _get_current_translation(pool, code, key)
    _, media_type = await _lang_media(pool, code, key)
    await state.set_state(LanguageStates.waiting_translation)
    await state.update_data(
        pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id,
        code=code, key=key, page=page,
    )
    await callback.message.edit_text(
        _tr_screen_text(key, code, ru, cur, media_type),
        parse_mode="HTML",
        reply_markup=_tr_edit_kb(code, page, key, bool(media_type)),
    )
    await callback.answer()


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tr_edit_kb(code: str, page: int, key: str, has_media: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_media:
        rows.append([InlineKeyboardButton(text="🗑 Убрать медиа", callback_data=f"lang:media_clear:{code}:{page}:{key}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку ключей", callback_data=f"lang:tr:{code}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _lang_media(pool, code: str, key: str) -> tuple[str | None, str | None]:
    """Медиа, заданное ИМЕННО для этого языка (без RU-наследования)."""
    if key not in _BOT_MESSAGE_KEYS:
        return None, None
    row = await pool.fetchrow(
        "SELECT file_id, file_type FROM bot_messages WHERE key = $1 AND lang = $2", key, code,
    )
    return (row["file_id"], row["file_type"]) if row else (None, None)


def _extract_media(message: Message) -> tuple[str | None, str | None]:
    if message.animation:
        return message.animation.file_id, "animation"
    if message.video:
        return message.video.file_id, "video"
    if message.photo:
        return message.photo[-1].file_id, "photo"
    return None, None


def _tr_screen_text(key: str, code: str, ru: str, cur: str, media_type: str | None) -> str:
    cur_preview = cur if cur else "<i>не задано → используется RU</i>"
    media_line = f"\n🎬 Медиа: <b>{media_type}</b> прикреплено" if media_type else ""
    hint = ""
    if key in _BOT_MESSAGE_KEYS:
        hint = "\n🎬 Можно прислать <b>видео / гиф / фото</b> — прикрепится к менюшке (подпись станет текстом)."
    return (
        f"✏️ <b>{key}</b>  [{code}]{media_line}\n\n"
        f"<b>RU:</b>\n<code>{_esc(ru)}</code>\n\n"
        f"<b>Текущий:</b>\n{_esc(cur_preview) if cur else cur_preview}\n\n"
        "Отправь новый текст. Чтобы вернуться к RU-фолбэку — отправь <code>-</code>."
        + hint
    )


@router.message(LanguageStates.waiting_translation)
async def handle_translation(message: Message, state: FSMContext):
    if not await has_permission(message.from_user.id, PERM_LANGUAGES):
        return
    data = await state.get_data()
    code, key = data["code"], data["key"]
    page = data.get("page", 0)
    # У медиа текст приходит в подписи, у обычного сообщения — в .text.
    # НЕ .strip() — обрезка ведущих пробелов сдвинула бы offset'ы энтитей (прем-эмодзи,
    # форматирование) и Телеграм отбросил бы часть из них. Для управляющих проверок
    # (пусто / «-») используем отдельный stripped-вариант.
    text = message.text or message.caption or ""
    text_cmd = text.strip()
    ents = message.entities or message.caption_entities
    media_file_id, media_type = _extract_media(message) if key in _BOT_MESSAGE_KEYS else (None, None)
    # Диагностика: что именно прислал админ — какие энтити (прем-эмодзи/формат) видим.
    logger.info(
        "TR-SAVE key=%s lang=%s text_len=%d entities=%s",
        key, code, len(text),
        [(e.type, e.offset, e.length, getattr(e, "custom_emoji_id", None)) for e in (ents or [])],
    )
    # Сообщение С МЕДИА не удаляем: оно остаётся источником гифки/видео в чате, его не
    # из чего пересоздать, если стереть. Чисто текстовое — убираем, чтобы не засорять.
    if not media_file_id:
        try:
            await message.delete()
        except Exception:
            pass
    pool = get_pool()

    if media_file_id and not text_cmd:
        # Прислали только медиа — прикрепляем, текст не трогаем.
        await set_bot_message_media(pool, key, media_file_id, media_type, code)
    elif text_cmd == "-":
        # `-` сбрасывает перевод → RU-фолбэк (и текст, и медиа этого языка).
        if key in _BOT_MESSAGE_KEYS:
            await pool.execute("DELETE FROM bot_messages WHERE key = $1 AND lang = $2", key, code)
        else:
            await pool.execute("DELETE FROM i18n_strings WHERE key = $1 AND lang = $2", key, code)
    else:
        entities_payload = [e.model_dump(exclude_none=True) for e in ents] if ents else []
        if key in _BOT_MESSAGE_KEYS:
            await set_bot_message(pool, key, text, entities_payload, code)
            if media_file_id:
                await set_bot_message_media(pool, key, media_file_id, media_type, code)
        else:
            # Обычные i18n-строки — без энтитей, можно сохранить очищенный текст.
            await set_i18n_string(pool, key, code, text_cmd)

    cur, ru = await _get_current_translation(pool, code, key)
    _, cur_media_type = await _lang_media(pool, code, key)
    try:
        await message.bot.edit_message_text(
            "✅ Сохранено.\n\n" + _tr_screen_text(key, code, ru, cur, cur_media_type),
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML",
            reply_markup=_tr_edit_kb(code, page, key, bool(cur_media_type)),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("lang:media_clear:"))
async def cb_lang_media_clear(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    # format: lang:media_clear:{code}:{page}:{key}
    parts = callback.data.split(":", 4)
    code = parts[2]
    try:
        page = int(parts[3])
        key = parts[4]
    except (IndexError, ValueError):
        page = 0
        key = parts[-1]
    pool = get_pool()
    await set_bot_message_media(pool, key, None, None, code)
    cur, ru = await _get_current_translation(pool, code, key)
    await state.set_state(LanguageStates.waiting_translation)
    await state.update_data(
        pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id,
        code=code, key=key, page=page,
    )
    await callback.message.edit_text(
        "✅ Медиа убрано.\n\n" + _tr_screen_text(key, code, ru, cur, None),
        parse_mode="HTML",
        reply_markup=_tr_edit_kb(code, page, key, False),
    )
    await callback.answer("Медиа убрано")


# ============ Buttons editor (bot_buttons per lang) ============

_BTN_STYLES = {"primary", "success", "danger"}


def _buttons_list_kb(code: str, buttons: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for b in buttons:
        rows.append([InlineKeyboardButton(
            text=f"🔘 {b['key']}",
            callback_data=f"lang:btn_edit:{code}:{b['key']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ К языку", callback_data=f"lang:view:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("lang:btn:"))
async def cb_lang_btn_list(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    await state.clear()
    code = callback.data.split(":", 2)[2]
    pool = get_pool()
    buttons = await get_all_bot_buttons(pool, code)
    await callback.message.edit_text(
        f"🔘 <b>Кнопки [{code}]</b>\n\nВыбери набор кнопок для редактирования:",
        parse_mode="HTML",
        reply_markup=_buttons_list_kb(code, buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:btn_edit:"))
async def cb_lang_btn_edit(callback: CallbackQuery, state: FSMContext):
    if not await _check(callback):
        return
    _, _, code, key = callback.data.split(":", 3)
    pool = get_pool()
    rows = await get_bot_buttons(pool, key, code)

    preview_lines = []
    for row in rows:
        row_texts = [f"[{b['text']}]" for b in row]
        preview_lines.append(" ".join(row_texts))
    preview_text = "\n".join(preview_lines) if preview_lines else "(пусто)"

    await state.set_state(LanguageStates.waiting_buttons)
    await state.update_data(
        pm_cid=callback.message.chat.id, pm_mid=callback.message.message_id,
        code=code, key=key,
    )
    await callback.message.edit_text(
        f"🔘 <b>{key}</b>  [{code}]\n\n"
        f"Текущие кнопки:\n{_esc(preview_text)}\n\n"
        "Отправь кнопки в формате:\n"
        "<code>Текст кнопки | https://url.com</code>\n"
        "<code>Текст кнопки | callback_data</code>\n\n"
        "💎 <b>Прем-эмодзи</b> работают как иконка кнопки: вставь его <b>в самое начало</b> "
        "текста кнопки — он автоматически станет иконкой (icon_custom_emoji_id). "
        "Юникод-фолбэк из текста срежется. "
        "⚠️ Работает только если у владельца бота есть Telegram Premium или бот купил доп. имя на Fragment.\n\n"
        "📋 <b>Доступные колбэки:</b>\n"
        "• <code>menu:open</code> — главное меню\n"
        "• <code>meme:start</code> — создать мем\n"
        "• <code>template:add_user</code> — добавить шаблон "
        "(<code>utpl:quick</code> — личный, <code>utpl:public</code> — публичный)\n"
        "• <code>settings:open</code> — настройки · <code>settings:language</code> — выбор языка\n"
        "• <code>premium:open</code> — Гифыч Premium · <code>premium:gift</code> — подарить\n"
        "• <code>premium:buy:1m:9</code> / <code>premium:buy:6m:15</code> / "
        "<code>premium:buy:12m:27</code> / <code>premium:buy:999y:49</code> — покупка\n"
        "• <code>op:check</code> — проверить ОП\n"
        "• <code>settings:back</code> · <code>premium:back</code> — назад\n"
        "• ссылка — просто вставь <code>https://…</code> вместо колбэка\n\n"
        "🎨 <b>Цвет</b> (опц.) — третий <code>|</code>: <code>primary</code>/<code>success</code>/<code>danger</code>\n"
        "Разные строки = разные ряды. Кнопки в одном ряду — через <code> || </code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить к RU", callback_data=f"lang:btn_reset:{code}:{key}")],
            [InlineKeyboardButton(text="◀️ К списку", callback_data=f"lang:btn:{code}")],
        ]),
    )
    await callback.answer()


def _parse_buttons(full_text: str, entities: list) -> list[list[dict]]:
    """Parse multi-line button definitions, extracting leading custom_emoji entities.

    Format per button: `Text | target | style?`
    Buttons in same row separated by `||`; different lines = different rows.
    If text starts with a custom_emoji entity, it becomes icon_custom_emoji_id and the
    Unicode fallback char(s) are stripped from the visible text.
    """
    # Python char index -> UTF-16 code-unit offset
    char_to_utf16 = [0]
    for ch in full_text:
        char_to_utf16.append(char_to_utf16[-1] + (2 if ord(ch) > 0xFFFF else 1))

    # custom_emoji entities (utf16 offset, utf16 length, emoji_id)
    custom_emojis = []
    for e in entities or []:
        e_type = e.type.value if hasattr(e.type, "value") else e.type
        if e_type == "custom_emoji":
            custom_emojis.append((e.offset, e.length, e.custom_emoji_id))

    def py_to_utf16(py_off: int) -> int:
        if py_off < 0:
            return 0
        if py_off >= len(char_to_utf16):
            return char_to_utf16[-1]
        return char_to_utf16[py_off]

    def emoji_at(py_off: int):
        """If a custom_emoji starts exactly at this Python offset, return (emoji_id, py_chars_to_strip)."""
        u_off = py_to_utf16(py_off)
        for u_start, u_len, eid in custom_emojis:
            if u_start == u_off:
                py_end = py_off
                consumed = 0
                while py_end < len(full_text) and consumed < u_len:
                    consumed += 2 if ord(full_text[py_end]) > 0xFFFF else 1
                    py_end += 1
                return eid, py_end - py_off
        return None, 0

    rows = []
    line_off = 0
    for line in full_text.split("\n"):
        line_start = line_off
        line_off += len(line) + 1  # +1 for newline

        if not line.strip():
            continue

        row = []
        col_cursor = line_start
        for btn_raw in line.split("||"):
            btn_off = col_cursor
            col_cursor += len(btn_raw) + 2  # +2 for "||" separator
            stripped_btn = btn_raw.strip()
            if not stripped_btn:
                continue
            leading_ws = len(btn_raw) - len(btn_raw.lstrip())
            text_start_off = btn_off + leading_ws

            # Legacy " - " syntax (no premium emoji support here)
            if " - " in stripped_btn and "|" not in stripped_btn:
                parts = [p.strip() for p in stripped_btn.split(" - ")]
            else:
                parts = [p.strip() for p in stripped_btn.split("|")]
            if len(parts) < 2:
                continue
            text_part, target = parts[0], parts[1]
            style = parts[2].lower() if len(parts) >= 3 and parts[2].lower() in _BTN_STYLES else None

            # Try to peel a leading custom emoji off text_part
            emoji_id, strip_chars = emoji_at(text_start_off)
            if emoji_id and strip_chars > 0 and strip_chars <= len(text_part):
                text_part = text_part[strip_chars:].lstrip()

            entry: dict = {"text": text_part}
            if emoji_id:
                entry["icon_custom_emoji_id"] = emoji_id
            if target.startswith(("http://", "https://", "tg://")):
                entry["url"] = target
            else:
                cb = target[9:] if target.startswith("callback:") else target
                entry["callback_data"] = cb
            if style:
                entry["style"] = style
            row.append(entry)
        if row:
            rows.append(row)
    return rows


@router.message(LanguageStates.waiting_buttons)
async def handle_buttons_input(message: Message, state: FSMContext):
    if not await has_permission(message.from_user.id, PERM_LANGUAGES):
        return
    data = await state.get_data()
    code, key = data["code"], data["key"]
    full_text = message.text or ""
    entities = message.entities or []

    rows = _parse_buttons(full_text, entities)

    try:
        await message.delete()
    except Exception:
        pass

    pool = get_pool()
    await set_bot_buttons(pool, key, rows, code)
    await state.clear()

    preview = "\n".join(" ".join(f"[{b['text']}]" for b in row) for row in rows) or "(пусто)"
    buttons = await get_all_bot_buttons(pool, code)
    try:
        await message.bot.edit_message_text(
            f"✅ Кнопки <b>{key}</b> [{code}] сохранены:\n<code>{_esc(preview)}</code>\n\n"
            "Выбери набор кнопок для редактирования:",
            chat_id=data["pm_cid"], message_id=data["pm_mid"],
            parse_mode="HTML",
            reply_markup=_buttons_list_kb(code, buttons),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("lang:btn_reset:"))
async def cb_lang_btn_reset(callback: CallbackQuery):
    if not await _check(callback):
        return
    _, _, code, key = callback.data.split(":", 3)
    pool = get_pool()
    await pool.execute(
        "DELETE FROM bot_buttons WHERE key = $1 AND lang = $2", key, code,
    )
    buttons = await get_all_bot_buttons(pool, code)
    await callback.message.edit_text(
        f"✅ Кнопки <b>{key}</b> [{code}] сброшены к RU-фолбэку.\n\n"
        "Выбери набор кнопок для редактирования:",
        parse_mode="HTML",
        reply_markup=_buttons_list_kb(code, buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "lang:noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
