"""Default RU strings for user-facing UI. Seeded into i18n_strings on startup.

Naming convention: section.purpose (e.g. tpl.added, tpl.duplicate).
When adding new user-facing string in code, add it here and call t("section.key", lang).
"""

DEFAULTS: dict[str, str] = {
    # --- General ---
    "common.menu": "🌐 Главное меню",
    "common.back": "⬅ Назад",
    "common.cancel": "❌ Отмена",
    "common.error": "❌ Что-то пошло не так, попробуй позже.",
    "common.go_menu": "◀️ В меню",
    "common.add_more": "➕ Добавить ещё",
    "common.use_in_chat": "🎬 Использовать в чате",
    "common.send_to_friend": "📤 Отправить другу",

    # --- Settings ---
    "settings.title": "⚙️ <b>Настройки</b>",
    "settings.language_btn": "🌐 Язык",
    "settings.language_choose": "🌐 Выберите язык / Choose language:",
    "settings.language_set": "✅ Язык изменён.",

    # --- Premium ---
    "premium.title": "💎 <b>Гифыч Premium</b>",
    "premium.text": (
        "💎 <b>Гифыч Premium</b>\n\n"
        "Преимущества Premium:\n"
        "• Без водяных знаков\n"
        "• Приоритет в очереди обработки\n"
        "• Бот @гифыч будет работать ещё быстрее\n"
        "• Расширенный доступ к шаблонам\n\n"
        "Выбери период подписки:"
    ),
    "premium.buy_1m": "⭐ 1 месяц — 9 ⭐",
    "premium.buy_6m": "⭐ 6 месяцев — 15 ⭐",
    "premium.buy_12m": "⭐ 12 месяцев — 27 ⭐",
    "premium.buy_999y": "⭐ 999 лет — 49 ⭐",
    "premium.stars_buy": "🛍 Купить Telegram Stars ↗",
    "premium.gift_btn": "🎁 Подарить Гифыч Premium",
    "premium.gift_alert": "Для подарка напишите @гифыч и укажите username получателя.",
    "premium.plan_1m": "Гифыч Premium — 1 месяц",
    "premium.plan_6m": "Гифыч Premium — 6 месяцев",
    "premium.plan_12m": "Гифыч Premium — 12 месяцев",
    "premium.plan_999y": "Гифыч Premium — 999 лет",
    "premium.invoice_desc": "Доступ к расширенным функциям @гифыч",
    "premium.activated": "✅ Оплата {stars} ⭐ получена!\nГифыч Premium активирован. Спасибо!",

    # --- Templates: add flow ---
    # (tpl.add_choose_type живёт в bot_messages — он медиа-способный, см. queries._MSG_DEFAULTS)
    "tpl.add_quick": "⚡ Быстро добавить",
    "tpl.add_public": "🌐 Публичный шаблон",
    "tpl.send_media": "Отправь GIF или фото для шаблона.",
    "tpl.send_tags": "Отлично! Теперь <b>отправьте от 1 до 5 смайликов</b> которые по вашему мнению, лучше всего описывают ваш шаблон.",
    "tpl.tags_invalid": "Пожалуйста, отправь от 1 до 5 <b>смайликов</b> (эмодзи).",
    "tpl.added_personal": "✅ Шаблон добавлен и доступен вам.",
    "tpl.sent_to_moderation": "⏳ Шаблон отправлен на модерацию.\nТеги: {tags}",
    "tpl.add_error": "❌ Ошибка при сохранении шаблона. Попробуй ещё раз.",
    "tpl.duplicate": "⚠️ Эта гифка уже добавлена в шаблоны.",
    "tpl.add_another": "➕ Добавить другую",

    # --- Templates: pin via deep link ---
    "tpl.pin_invalid": "Неверная ссылка на шаблон",
    "tpl.pin_not_found": "❌ Шаблон не найден или недоступен",
    "tpl.pin_added": (
        "✅ Шаблон <b>{title}</b> добавлен — он будет первым в твоей выдаче.\n\n"
        "Открой инлайн: <code>@{bot_username}</code>"
    ),
    "tpl.no_title": "Без названия",

    # --- Meme flow ---
    "meme.send_text": "✏️ Напиши текст для наложения (до 100 символов):",
    "meme.send_text_short": "✏️ Напиши текст для наложения:",
    "meme.choose": "🌐 Выбери шаблон:",
    "meme.none": "Шаблонов пока нет.",
    "meme.tpl_unavailable": "Шаблон недоступен",
    "meme.tpl_not_found": "Шаблон не найден или недоступен",
    "meme.processing": "⏳ Обрабатываю...",
    "meme.too_long": "Максимум 100 символов",
    "meme.restart": "Что-то пошло не так, начни заново — /start",
    "meme.error": "❌ Ошибка: {err}",

    # --- Start / OP ---
    "start.bad_link": "Неверная ссылка",
    "inline.no_templates": "Шаблонов нет — добавь через /admin",
    "inline.next_meme": "🔁 Ещё мем",

    # --- OP recheck ---
    "op.not_subscribed": "Не подписан на: {channels}",
    "op.error": "Ошибка, попробуй снова",
}


# English translations. Selecting a language with no DB override falls back here
# (see i18n.t + get_bot_message/get_bot_buttons), so English works out of the box
# without the admin having to translate every string by hand.
DEFAULTS_EN: dict[str, str] = {
    # --- General ---
    "common.menu": "🌐 Main menu",
    "common.back": "⬅ Back",
    "common.cancel": "❌ Cancel",
    "common.error": "❌ Something went wrong, try again later.",
    "common.go_menu": "◀️ To menu",
    "common.add_more": "➕ Add another",
    "common.use_in_chat": "🎬 Use in chat",
    "common.send_to_friend": "📤 Send to a friend",

    # --- Settings ---
    "settings.title": "⚙️ <b>Settings</b>",
    "settings.language_btn": "🌐 Language",
    "settings.language_choose": "🌐 Выберите язык / Choose language:",
    "settings.language_set": "✅ Language changed.",

    # --- Premium ---
    "premium.title": "💎 <b>Gifych Premium</b>",
    "premium.text": (
        "💎 <b>Gifych Premium</b>\n\n"
        "Premium perks:\n"
        "• No watermarks\n"
        "• Priority in the processing queue\n"
        "• @гифыч works even faster\n"
        "• Extended access to templates\n\n"
        "Choose a subscription period:"
    ),
    "premium.buy_1m": "⭐ 1 month — 9 ⭐",
    "premium.buy_6m": "⭐ 6 months — 15 ⭐",
    "premium.buy_12m": "⭐ 12 months — 27 ⭐",
    "premium.buy_999y": "⭐ 999 years — 49 ⭐",
    "premium.stars_buy": "🛍 Buy Telegram Stars ↗",
    "premium.gift_btn": "🎁 Gift Gifych Premium",
    "premium.gift_alert": "To gift, message @гифыч with the recipient's username.",
    "premium.plan_1m": "Gifych Premium — 1 month",
    "premium.plan_6m": "Gifych Premium — 6 months",
    "premium.plan_12m": "Gifych Premium — 12 months",
    "premium.plan_999y": "Gifych Premium — 999 years",
    "premium.invoice_desc": "Access to extended @гифыч features",
    "premium.activated": "✅ Payment of {stars} ⭐ received!\nGifych Premium is active. Thank you!",

    # --- Templates: add flow ---
    # (tpl.add_choose_type lives in bot_messages — media-capable, see queries._MSG_DEFAULTS)
    "tpl.add_quick": "⚡ Quick add",
    "tpl.add_public": "🌐 Public template",
    "tpl.send_media": "Send a GIF or photo for the template.",
    "tpl.send_tags": "Great! Now <b>send 1 to 5 emoji</b> that best describe your template.",
    "tpl.tags_invalid": "Please send 1 to 5 <b>emoji</b>.",
    "tpl.added_personal": "✅ Template added and available to you.",
    "tpl.sent_to_moderation": "⏳ Template sent for moderation.\nTags: {tags}",
    "tpl.add_error": "❌ Error while saving the template. Try again.",
    "tpl.duplicate": "⚠️ This gif is already in the templates.",
    "tpl.add_another": "➕ Add another",

    # --- Templates: pin via deep link ---
    "tpl.pin_invalid": "Invalid template link",
    "tpl.pin_not_found": "❌ Template not found or unavailable",
    "tpl.pin_added": (
        "✅ Template <b>{title}</b> added — it'll be first in your results.\n\n"
        "Open inline: <code>@{bot_username}</code>"
    ),
    "tpl.no_title": "Untitled",

    # --- Meme flow ---
    "meme.send_text": "✏️ Type the overlay text (up to 100 characters):",
    "meme.send_text_short": "✏️ Type the overlay text:",
    "meme.choose": "🌐 Choose a template:",
    "meme.none": "No templates yet.",
    "meme.tpl_unavailable": "Template unavailable",
    "meme.tpl_not_found": "Template not found or unavailable",
    "meme.processing": "⏳ Processing...",
    "meme.too_long": "100 characters max",
    "meme.restart": "Something went wrong, start over — /start",
    "meme.error": "❌ Error: {err}",

    # --- Start / OP ---
    "start.bad_link": "Invalid link",
    "inline.no_templates": "No templates — add via /admin",
    "inline.next_meme": "🔁 Another meme",

    # --- OP recheck ---
    "op.not_subscribed": "Not subscribed to: {channels}",
    "op.error": "Error, try again",
}


# All shipped translations keyed by language code. Used by the startup seeder.
DEFAULTS_BY_LANG: dict[str, dict[str, str]] = {
    "ru": DEFAULTS,
    "en": DEFAULTS_EN,
}
