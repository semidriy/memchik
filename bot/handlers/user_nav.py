from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery, Message, MessageEntity,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaAnimation, InputMediaVideo, InputMediaPhoto,
    LabeledPrice, PreCheckoutQuery,
)

from bot.database import get_pool
from bot.database.queries import (
    get_bot_buttons, get_bot_message,
    get_languages, set_user_lang,
    set_user_bot_premium,
)
from bot.keyboards.user import build_inline_kb, main_menu
from bot.services.i18n import t, user_lang, invalidate_lang_cache

router = Router()


def _msg_entities(msg: dict):
    if msg.get("entities"):
        return [MessageEntity(**{k: v for k, v in e.items()}) for e in msg["entities"]]
    return None


def _input_media(file_type: str | None, file_id: str, caption: str, entities):
    kw = {"media": file_id, "caption": caption or None}
    if entities:
        kw["caption_entities"] = entities
    else:
        kw["parse_mode"] = "HTML"
    if file_type == "video":
        return InputMediaVideo(**kw)
    if file_type == "photo":
        return InputMediaPhoto(**kw)
    return InputMediaAnimation(**kw)


async def _answer_media(message: Message, file_type: str | None, file_id: str, caption: str, entities, kb):
    kw = {"caption": caption or None, "reply_markup": kb}
    if entities:
        kw["caption_entities"] = entities
    else:
        kw["parse_mode"] = "HTML"
    if file_type == "video":
        await message.answer_video(file_id, **kw)
    elif file_type == "photo":
        await message.answer_photo(file_id, **kw)
    else:
        await message.answer_animation(file_id, **kw)


async def _edit_with_msg(callback: CallbackQuery, msg: dict, fallback_text: str, kb: InlineKeyboardMarkup):
    """Render a menu from a stored bot_message: entities-aware, и с опциональным медиа.
    Меняем текст на медиа (и обратно) пересозданием сообщения, т.к. edit_text/edit_media
    не умеют конвертировать тип сообщения."""
    text = msg["text"] or fallback_text
    entities = _msg_entities(msg)
    file_id = msg.get("file_id")
    file_type = msg.get("file_type")
    m = callback.message
    cur_is_media = m.content_type in ("animation", "video", "photo")

    if file_id:
        if cur_is_media:
            try:
                await m.edit_media(_input_media(file_type, file_id, text, entities), reply_markup=kb)
                return
            except TelegramBadRequest:
                pass
        try:
            await m.delete()
        except Exception:
            pass
        await _answer_media(m, file_type, file_id, text, entities, kb)
        return

    # Текстовая менюшка
    if cur_is_media:
        try:
            await m.delete()
        except Exception:
            pass
        if entities:
            await m.answer(text, entities=entities, parse_mode=None, reply_markup=kb)
        else:
            await m.answer(text, parse_mode="HTML", reply_markup=kb)
        return
    if entities:
        await m.edit_text(text, entities=entities, parse_mode=None, reply_markup=kb)
    else:
        await m.edit_text(text, parse_mode="HTML", reply_markup=kb)


def _settings_default_kb(lang_btn: str, complaint_btn: str, ads_btn: str, back_btn: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lang_btn, callback_data="settings:language")],
        [InlineKeyboardButton(text=complaint_btn, url="https://t.me/your_support_channel")],
        [InlineKeyboardButton(text=ads_btn, url="https://t.me/your_ads_channel")],
        [InlineKeyboardButton(text=back_btn, callback_data="settings:back")],
    ])


_PLAN_KEYS = {
    "1m": "premium.plan_1m",
    "6m": "premium.plan_6m",
    "12m": "premium.plan_12m",
    "999y": "premium.plan_999y",
}


@router.callback_query(F.data == "settings:open")
async def cb_settings(callback: CallbackQuery):
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    msg = await get_bot_message(pool, "settings", lang)
    btn_rows = await get_bot_buttons(pool, "settings_menu", lang)
    kb = build_inline_kb(btn_rows) if btn_rows else _settings_default_kb(
        await t("settings.language_btn", lang),
        "🤖 Оставить жалобу ↗",
        "Купить рекламу в Гифыч ↗",
        await t("common.back", lang),
    )
    await _edit_with_msg(callback, msg, await t("settings.title", lang), kb)
    await callback.answer()


@router.callback_query(F.data == "settings:language")
async def cb_language(callback: CallbackQuery):
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    langs = await get_languages(pool, active_only=True)
    rows = []
    for L in langs:
        marker = "✅ " if L["code"] == lang else ""
        rows.append([InlineKeyboardButton(
            text=f"{marker}{L['flag_emoji']} {L['name']}",
            callback_data=f"settings:setlang:{L['code']}",
        )])
    rows.append([InlineKeyboardButton(text=await t("common.back", lang), callback_data="settings:open")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(
        await t("settings.language_choose", lang),
        parse_mode="HTML", reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings:setlang:"))
async def cb_set_language(callback: CallbackQuery):
    pool = get_pool()
    code = callback.data.split(":", 2)[2]
    langs = {L["code"] for L in await get_languages(pool, active_only=True)}
    if code not in langs:
        await callback.answer("?", show_alert=False)
        return
    await set_user_lang(pool, callback.from_user.id, code)
    invalidate_lang_cache(callback.from_user.id)
    await callback.answer(await t("settings.language_set", code), show_alert=False)
    msg = await get_bot_message(pool, "settings", code)
    btn_rows = await get_bot_buttons(pool, "settings_menu", code)
    kb = build_inline_kb(btn_rows) if btn_rows else _settings_default_kb(
        await t("settings.language_btn", code),
        "🤖 Оставить жалобу ↗",
        "Купить рекламу в Гифыч ↗",
        await t("common.back", code),
    )
    try:
        await _edit_with_msg(callback, msg, await t("settings.title", code), kb)
    except Exception:
        pass


@router.callback_query(F.data == "menu:open")
async def cb_menu_open(callback: CallbackQuery):
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    msg = await get_bot_message(pool, "welcome", lang)
    btn_rows = await get_bot_buttons(pool, "main_menu", lang)
    kb = build_inline_kb(btn_rows) if btn_rows else main_menu()
    await _edit_with_msg(callback, msg, await t("common.menu", lang), kb)
    await callback.answer()


@router.callback_query(F.data == "settings:back")
async def cb_settings_back(callback: CallbackQuery):
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    msg = await get_bot_message(pool, "welcome", lang)
    btn_rows = await get_bot_buttons(pool, "main_menu", lang)
    kb = build_inline_kb(btn_rows) if btn_rows else main_menu()
    await _edit_with_msg(callback, msg, await t("common.menu", lang), kb)
    await callback.answer()


@router.callback_query(F.data == "premium:open")
async def cb_premium(callback: CallbackQuery):
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    msg = await get_bot_message(pool, "premium", lang)
    btn_rows = await get_bot_buttons(pool, "premium_menu", lang)
    if btn_rows:
        kb = build_inline_kb(btn_rows)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=await t("premium.buy_1m", lang), callback_data="premium:buy:1m:9")],
            [InlineKeyboardButton(text=await t("premium.buy_6m", lang), callback_data="premium:buy:6m:15")],
            [InlineKeyboardButton(text=await t("premium.buy_12m", lang), callback_data="premium:buy:12m:27")],
            [InlineKeyboardButton(text=await t("premium.buy_999y", lang), callback_data="premium:buy:999y:49")],
            [InlineKeyboardButton(text=await t("premium.stars_buy", lang), url="https://t.me/stars")],
            [InlineKeyboardButton(text=await t("premium.gift_btn", lang), callback_data="premium:gift")],
            [InlineKeyboardButton(text=await t("common.back", lang), callback_data="premium:back")],
        ])
    await _edit_with_msg(callback, msg, await t("premium.text", lang), kb)
    await callback.answer()


@router.callback_query(F.data == "premium:back")
async def cb_premium_back(callback: CallbackQuery):
    pool = get_pool()
    lang = await user_lang(callback.from_user.id)
    msg = await get_bot_message(pool, "welcome", lang)
    btn_rows = await get_bot_buttons(pool, "main_menu", lang)
    kb = build_inline_kb(btn_rows) if btn_rows else main_menu()
    await _edit_with_msg(callback, msg, await t("common.menu", lang), kb)
    await callback.answer()


@router.callback_query(F.data.startswith("premium:buy:"))
async def cb_premium_buy(callback: CallbackQuery):
    lang = await user_lang(callback.from_user.id)
    parts = callback.data.split(":")
    plan, stars_str = parts[2], parts[3]
    stars = int(stars_str)
    plan_key = _PLAN_KEYS.get(plan, "premium.title")
    label = await t(plan_key, lang)
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=label,
        description=await t("premium.invoice_desc", lang),
        payload=f"premium_{plan}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=stars)],
    )
    await callback.answer()


@router.callback_query(F.data == "premium:gift")
async def cb_premium_gift(callback: CallbackQuery):
    lang = await user_lang(callback.from_user.id)
    await callback.answer(await t("premium.gift_alert", lang), show_alert=True)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


_PLAN_DAYS = {
    "premium_1m": 30,
    "premium_6m": 180,
    "premium_12m": 365,
    "premium_999y": 999 * 365,
}


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    lang = await user_lang(message.from_user.id)
    payload = message.successful_payment.invoice_payload
    stars = message.successful_payment.total_amount
    days = _PLAN_DAYS.get(payload, 30)
    until = datetime.now(timezone.utc) + timedelta(days=days)
    pool = get_pool()
    await set_user_bot_premium(pool, message.from_user.id, until)
    await message.answer(await t("premium.activated", lang, stars=stars))
