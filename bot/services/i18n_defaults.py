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
    "tpl.add_choose_type": (
        "🖼 Каким способом добавить шаблон?\n\n"
        "⚡ <b>Быстро (только для вас)</b> — сразу доступен, не проходит модерацию.\n\n"
        "🌐 <b>Публичный</b> — проходит модерацию, после одобрения виден всем."
    ),
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
