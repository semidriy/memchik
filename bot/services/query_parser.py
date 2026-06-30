import re

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "☀-⛿"
    "✀-➿"
    "︀-️"
    "\U0001F1E0-\U0001F1FF"
    "⃣"
    "]+",
    flags=re.UNICODE,
)


def parse_inline_query(raw: str) -> tuple[list[str], str]:
    """
    "👋 привет"  ->  (["👋"], "привет")
    "привет"     ->  ([], "привет")
    "👋"         ->  (["👋"], "")
    ""           ->  ([], "")
    """
    text = raw.strip()
    if not text:
        return [], ""

    parts = text.split(None, 1)
    first = parts[0]

    if not _EMOJI_RE.sub("", first).strip():
        emojis = _EMOJI_RE.findall(first)
        overlay = parts[1].strip() if len(parts) > 1 else ""
        return emojis, overlay

    return [], text


def extract_emojis(text: str) -> list[str]:
    return _EMOJI_RE.findall(text)
