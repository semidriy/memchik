from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)

BTN_BROADCAST = "⚡️Рассылка"
BTN_OP = "⭐️ОП"
BTN_LINKS = "🖇Ссылки"
BTN_TEMPLATES = "Шаблоны"          # скрыт из панели, но доступен по колбэку
BTN_STATS = "📊Статистика"
BTN_DATABASE = "📲БД"
BTN_MESSAGES = "💌Кнопки Гл.меню"
BTN_PLACEMENTS = "💎Показы чаты"
BTN_DISPLAYS = "👁Показы бот"
BTN_ADMINS = "🙎‍♂️Админы"
BTN_LANGUAGES = "🌐Языки"
BTN_CLOSE = "👆Закрыть панель👆"

ADMIN_BUTTONS = {
    BTN_BROADCAST, BTN_OP, BTN_LINKS, BTN_TEMPLATES,
    BTN_STATS, BTN_DATABASE, BTN_PLACEMENTS, BTN_DISPLAYS, BTN_ADMINS,
    BTN_LANGUAGES, BTN_CLOSE,
}

# Map reply-button labels to permission keys
BTN_TO_PERM: dict[str, str] = {
    BTN_BROADCAST: "broadcast",
    BTN_OP: "op",
    BTN_LINKS: "links",
    BTN_TEMPLATES: "templates",
    BTN_STATS: "stats",
    BTN_DATABASE: "database",
    BTN_PLACEMENTS: "placements",
    BTN_DISPLAYS: "displays",
    BTN_ADMINS: "manage_admins",
    BTN_LANGUAGES: "languages",
}

# Порядок кнопок в панели (BTN_TEMPLATES скрыт — доступен только через колбэк admin:templates)
_PANEL_ORDER = [
    BTN_BROADCAST, BTN_LINKS,
    BTN_STATS,     BTN_OP,
    BTN_DISPLAYS,  BTN_DATABASE,
    BTN_PLACEMENTS,
    BTN_TEMPLATES, BTN_ADMINS,
    BTN_LANGUAGES,
]


def admin_reply_kb(allowed_buttons: set[str] | None = None) -> ReplyKeyboardMarkup:
    """Build the admin reply keyboard, filtered by which buttons the user has permission for."""
    btns = _PANEL_ORDER if allowed_buttons is None else [b for b in _PANEL_ORDER if b in allowed_buttons]
    keyboard: list[list[KeyboardButton]] = []
    for i in range(0, len(btns), 2):
        row = [KeyboardButton(text=btns[i])]
        if i + 1 < len(btns):
            row.append(KeyboardButton(text=btns[i + 1]))
        keyboard.append(row)
    keyboard.append([KeyboardButton(text=BTN_CLOSE)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def admin_remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def broadcast_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Новая рассылка", callback_data="broadcast:new")],
        [InlineKeyboardButton(text="История рассылок", callback_data="broadcast:history")],
    ])


def broadcast_preview(broadcast_id: int, has_buttons: bool = False) -> InlineKeyboardMarkup:
    btn_label = "✏️ Изменить кнопки" if has_buttons else "➕ Добавить кнопки"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать пост", callback_data=f"broadcast:edit:{broadcast_id}")],
        [InlineKeyboardButton(text=btn_label, callback_data=f"broadcast:buttons:{broadcast_id}")],
        [
            InlineKeyboardButton(text="🚀 Запустить", callback_data=f"broadcast:run:{broadcast_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"broadcast:cancel:{broadcast_id}"),
        ],
    ])


def broadcast_running(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить прогресс", callback_data=f"broadcast:progress:{broadcast_id}")],
        [InlineKeyboardButton(text="🛑 Остановить рассылку", callback_data=f"broadcast:stop:{broadcast_id}")],
    ])


def broadcast_stopped() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="История", callback_data="broadcast:history")],
        [InlineKeyboardButton(text="Новая рассылка", callback_data="broadcast:new")],
    ])


def op_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Список каналов", callback_data="op:list")],
        [InlineKeyboardButton(text="Добавить канал", callback_data="op:add")],
    ])


def op_channel_item(channel_id: int, title: str, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Выключить" if is_active else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"op:toggle:{channel_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"op:delete:{channel_id}"),
        ],
        [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"op:rename:{channel_id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="op:list")],
    ])


def op_list(channels: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        status = "🟢" if ch["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {ch['title']}",
            callback_data=f"op:view:{ch['id']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:op")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def links_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать ссылку", callback_data="links:new")],
        [InlineKeyboardButton(text="Все ссылки", callback_data="links:list")],
    ])


def link_item(link_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Деакт." if is_active else "🟢 Акт."
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"links:stats:{link_id}"),
            InlineKeyboardButton(text=toggle_text, callback_data=f"links:toggle:{link_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"links:delete:{link_id}"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="links:list")],
    ])


def link_delete_confirm(link_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"links:delete_confirm:{link_id}"),
            InlineKeyboardButton(text="◀️ Отмена", callback_data=f"links:view:{link_id}"),
        ],
    ])


def links_list(links: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for link in links:
        status = "🟢" if link["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {link['name']}",
            callback_data=f"links:view:{link['id']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:links")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def templates_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить шаблон", callback_data="tpl:add")],
        [InlineKeyboardButton(text="Список шаблонов", callback_data="tpl:list:0")],
    ])


def templates_list(templates: list[dict], page: int, total: int, per_page: int = 8) -> InlineKeyboardMarkup:
    rows = []
    for t in templates:
        mod = t.get("moderation_status", "approved")
        if mod == "pending":
            status = "⏳"
        elif mod == "rejected":
            status = "❌"
        elif t["is_active"]:
            status = "🟢"
        else:
            status = "🔴"
        icon = "🎬" if t["file_type"] == "animation" else "🖼"
        pub = "" if t.get("is_public") else "👤"
        title = t["title"] or "Без названия"
        rows.append([InlineKeyboardButton(
            text=f"{status}{icon}{pub} {title} ({t['use_count']})",
            callback_data=f"tpl:view:{t['id']}:{page}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"tpl:list:{page - 1}"))
    total_pages = max(1, (total + per_page - 1) // per_page)
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"tpl:list:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:templates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def template_item(template_id: int, is_active: bool, page: int = 0) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Выключить" if is_active else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"tpl:toggle:{template_id}:{page}"),
            InlineKeyboardButton(text="🏷 Теги", callback_data=f"tpl:tags:{template_id}:{page}"),
            InlineKeyboardButton(text="🗑", callback_data=f"tpl:delete:{template_id}:{page}"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data=f"tpl:list:{page}")],
    ])


def moderation_kb(template_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod:approve:{template_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:reject:{template_id}"),
        ],
        [InlineKeyboardButton(text="✏️ Отклонить с комментарием", callback_data=f"mod:comment:{template_id}")],
    ])


def stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="stats:refresh")],
    ])


def database_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Выгрузить CSV", callback_data="db:export")],
        [InlineKeyboardButton(text="🔓 Сбросить блокировки", callback_data="db:unblock_all")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="db:refresh")],
    ])


def placement_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новое размещение", callback_data="plc:new")],
        [InlineKeyboardButton(text="📋 Список", callback_data="plc:list")],
    ])


def placement_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="plc:skip")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="plc:cancel")],
    ])


def placement_duration_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="12 ч", callback_data="plc:dur:12"),
            InlineKeyboardButton(text="24 ч", callback_data="plc:dur:24"),
            InlineKeyboardButton(text="48 ч", callback_data="plc:dur:48"),
        ],
        [
            InlineKeyboardButton(text="7 дн", callback_data="plc:dur:168"),
            InlineKeyboardButton(text="30 дн", callback_data="plc:dur:720"),
        ],
        [InlineKeyboardButton(text="✏️ Ввести вручную (часы)", callback_data="plc:dur:manual")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="plc:cancel")],
    ])


def placement_limit_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ По умолчанию", callback_data="plc:skip")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="plc:cancel")],
    ])


def placement_list(placements: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for p in placements:
        status = "🟢" if p["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {p['name']}",
            callback_data=f"plc:view:{p['id']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:placements")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def placement_item(placement_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Выключить" if is_active else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"plc:toggle:{placement_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"plc:delete:{placement_id}"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="plc:list")],
    ])


def display_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новый показ", callback_data="dsp:new")],
        [InlineKeyboardButton(text="📋 Список", callback_data="dsp:list")],
    ])


def display_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="dsp:skip")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="dsp:cancel")],
    ])


def display_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="dsp:cancel")],
    ])


def display_list(displays: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for d in displays:
        status = "🟢" if d["is_active"] else "🔴"
        media = "🎬" if d.get("file_type") == "animation" else ("🖼" if d.get("file_type") == "photo" else "📝")
        rows.append([InlineKeyboardButton(
            text=f"{status}{media} {d['name']}",
            callback_data=f"dsp:view:{d['id']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:displays")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def display_item(display_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Выключить" if is_active else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=toggle_text, callback_data=f"dsp:toggle:{display_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"dsp:delete:{display_id}"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="dsp:list")],
    ])


def admins_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить админа", callback_data="adm:new")],
        [InlineKeyboardButton(text="📋 Список", callback_data="adm:list")],
    ])


def admins_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:cancel")],
    ])


def admins_list(admins: list[dict], super_admin_ids: list[int]) -> InlineKeyboardMarkup:
    rows = []
    for sid in super_admin_ids:
        rows.append([InlineKeyboardButton(
            text=f"👑 super • {sid}",
            callback_data="adm:noop",
        )])
    for a in admins:
        name = a.get("first_name") or a.get("username") or str(a["user_id"])
        perms_count = len(a.get("permissions") or [])
        rows.append([InlineKeyboardButton(
            text=f"👤 {name} • прав: {perms_count}",
            callback_data=f"adm:view:{a['user_id']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:admins")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def languages_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить язык", callback_data="lang:new")],
        [InlineKeyboardButton(text="📋 Список", callback_data="lang:list")],
    ])


def languages_list(langs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for L in langs:
        status = "🟢" if L["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{status} {L['flag_emoji']} {L['name']} [{L['code']}]",
            callback_data=f"lang:view:{L['code']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:languages")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def language_item(code: str, is_active: bool) -> InlineKeyboardMarkup:
    toggle = "🔴 Выключить" if is_active else "🟢 Включить"
    rows = [
        [InlineKeyboardButton(text="✏️ Тексты", callback_data=f"lang:tr:{code}:0")],
        [InlineKeyboardButton(text="🔘 Кнопки", callback_data=f"lang:btn:{code}")],
        [
            InlineKeyboardButton(text=toggle, callback_data=f"lang:toggle:{code}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"lang:delete:{code}"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="lang:list")],
    ]
    if code == "ru":
        # Не позволяем выключать/удалять базовый язык
        rows[2] = [InlineKeyboardButton(text="🔒 Базовый язык", callback_data="lang:noop")]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def languages_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="lang:cancel")],
    ])


def admin_perms_kb(user_id: int, current: set[str], all_perms: list[str], labels: dict[str, str]) -> InlineKeyboardMarkup:
    rows = []
    for perm in all_perms:
        mark = "✅" if perm in current else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {labels.get(perm, perm)}",
            callback_data=f"adm:toggle:{user_id}:{perm}",
        )])
    rows.append([InlineKeyboardButton(text="🗑 Удалить админа", callback_data=f"adm:remove:{user_id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="adm:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
