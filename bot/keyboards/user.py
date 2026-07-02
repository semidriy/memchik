from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def op_check_keyboard(channels: list[dict], link_code: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    _icons = {"channel": "📢", "bot": "🤖", "link": "🔗", "folder": "📁"}
    for ch in channels:
        ch_type = ch.get("channel_type", "channel")
        icon = _icons.get(ch_type, "📢")
        link = ch.get("invite_link") or (
            f"https://t.me/{ch['channel_username'].lstrip('@')}" if ch.get("channel_username") else ""
        )
        if link:
            rows.append([InlineKeyboardButton(text=f"{icon} {ch['title']}", url=link)])
    check_data = f"op:check:{link_code}" if link_code else "op:check"
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data=check_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 Создать мем", callback_data="meme:start")],
    ])


def templates_page(templates: list[dict], page: int, total: int, per_page: int = 8) -> InlineKeyboardMarkup:
    rows = []
    for t in templates:
        icon = "🎬" if t["file_type"] == "animation" else "🖼"
        title = t["title"] or "Без названия"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {title}",
            callback_data=f"meme:pick:{t['id']}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"meme:page:{page - 1}"))
    total_pages = max(1, (total + per_page - 1) // per_page)
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"meme:page:{page + 1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


_VALID_STYLES = {"primary", "success", "danger"}


def build_inline_kb(rows: list[list[dict]]) -> InlineKeyboardMarkup:
    kb_rows = []
    for row in rows:
        kb_row = []
        for btn in row:
            style = btn.get("style") if isinstance(btn.get("style"), str) and btn["style"] in _VALID_STYLES else None
            emoji_id = btn.get("icon_custom_emoji_id")
            kwargs = {"text": btn["text"]}
            if style:
                kwargs["style"] = style
            if emoji_id:
                kwargs["icon_custom_emoji_id"] = emoji_id
            if "url" in btn:
                kb_row.append(InlineKeyboardButton(url=btn["url"], **kwargs))
            elif "callback_data" in btn:
                kb_row.append(InlineKeyboardButton(callback_data=btn["callback_data"], **kwargs))
        if kb_row:
            kb_rows.append(kb_row)
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


def meme_add_text_button(bot_username: str, template_id: int | str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✏️ Добавить текст",
            url=f"https://t.me/{bot_username}?start=gif_{template_id}",
        )]
    ])
