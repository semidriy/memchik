import re

# Полный диапазон эмодзи по Unicode. ВАЖНО: и теги шаблонов (admin → extract_emojis),
# и поиск в чате (parse_inline_query) используют ОДНУ И ТУ ЖЕ регулярку — поэтому она
# обязана узнавать любой эмодзи, который клиент может вписать в тег. Старая версия не
# знала кучу частых символов (⭐ ⏰ ⌚ ⏳ ⭕ 🆗, стрелки ⬆️↔️, ‼️ ™️ и т.д.): при правке
# тега через админку extract_emojis возвращал [] → теги затирались → в чате по этому
# смайлику ничего не находилось. Ниже — расширенный набор всех эмодзи-блоков.
_EMOJI_RE = re.compile(
    "["
    "\U000000A9\U000000AE"            # © ®
    "\U0000203C\U00002049"            # ‼ ⁉
    "\U00002122\U00002139"            # ™ ℹ
    "\U00002194-\U00002199"           # ↔ ↕ ↖ … стрелки
    "\U000021A9-\U000021AA"           # ↩ ↪
    "\U0000231A-\U0000231B"           # ⌚ ⌛
    "\U00002328"                      # ⌨
    "\U000023CF"                      # ⏏
    "\U000023E9-\U000023F3"           # ⏩ … ⏰ ⏳ (медиа-кнопки, часы, песочные часы)
    "\U000023F8-\U000023FA"           # ⏸ ⏹ ⏺
    "\U000024C2"                      # Ⓜ
    "\U000025AA-\U000025AB"           # ▪ ▫
    "\U000025B6\U000025C0"            # ▶ ◀
    "\U000025FB-\U000025FE"           # ◻ ◼ …
    "\U00002600-\U000027BF"           # ☀ … ❤ ✂ ✅ ✨ ➗ (misc symbols + dingbats)
    "\U00002934-\U00002935"           # ⤴ ⤵
    "\U00002B00-\U00002BFF"           # ⬅ ⬆ ⬇ ⬛ ⭐ ⭕ … (стрелки/звёзды/фигуры)
    "\U00003030\U0000303D"            # 〰 〽
    "\U00003297\U00003299"            # ㊗ ㊙
    "\U0000FE00-\U0000FE0F"           # вариационные селекторы (︎ ️)
    "\U0000200D"                      # zero-width joiner (составные эмодзи: 👨‍👩‍👧)
    "\U000020E3"                      # ⃣ (комбинируемый keycap)
    "\U0001F000-\U0001FAFF"           # все эмодзи-блоки SMP (🀄 🃏 🆗 😀 🤔 🦄 🫡 …)
    "]+",
    flags=re.UNICODE,
)


# Теги в БД сравниваются с поиском ПОСТРОЧНО (tags && array — точное равенство строк).
# Поэтому обе стороны обязаны приводить эмодзи к ОДНОЙ канонической форме:
#  - слитную строку "😂🤣" режем на отдельные эмодзи (раньше findall возвращал весь
#    «ран» одной строкой → тег "😂🤣" не находился по "😂");
#  - выкидываем вариационные селекторы FE0E/FE0F — один клиент шлёт "⭐️" (со
#    селектором), другой "⭐" (без), байтово это разные строки.
_VS = {0xFE0E, 0xFE0F}
_ZWJ = 0x200D
_KEYCAP = 0x20E3
_SKIN_LO, _SKIN_HI = 0x1F3FB, 0x1F3FF          # модификаторы тона кожи
_REG_LO, _REG_HI = 0x1F1E6, 0x1F1FF            # региональные индикаторы (флаги 🇷🇺)


def _split_run(run: str) -> list[str]:
    """Порезать слитный ран эмодзи на отдельные кластеры: пары флагов, тона кожи,
    ZWJ-последовательности (👨‍👩‍👧) остаются одним эмодзи; FE0E/FE0F выбрасываются."""
    out: list[str] = []
    i, n = 0, len(run)
    while i < n:
        cp = ord(run[i])
        if cp in _VS or cp == _ZWJ or cp == _KEYCAP:
            i += 1  # осиротевший модификатор без базового символа — пропускаем
            continue
        cluster = [run[i]]
        i += 1
        if _REG_LO <= cp <= _REG_HI and i < n and _REG_LO <= ord(run[i]) <= _REG_HI:
            cluster.append(run[i])  # флаг = ровно пара региональных индикаторов
            i += 1
        else:
            while i < n:
                nc = ord(run[i])
                if nc in _VS:
                    i += 1  # каноническая форма — без селекторов
                elif _SKIN_LO <= nc <= _SKIN_HI or nc == _KEYCAP:
                    cluster.append(run[i])
                    i += 1
                elif nc == _ZWJ and i + 1 < n and ord(run[i + 1]) not in _VS and ord(run[i + 1]) != _ZWJ:
                    cluster.append(run[i])
                    cluster.append(run[i + 1])
                    i += 2
                else:
                    break
        out.append("".join(cluster))
    return out


def extract_emojis(text: str) -> list[str]:
    """Все эмодзи текста, по одному, в канонической форме (без дублей)."""
    out: list[str] = []
    for run in _EMOJI_RE.findall(text):
        for e in _split_run(run):
            if e not in out:
                out.append(e)
    return out


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
        emojis = extract_emojis(first)
        overlay = parts[1].strip() if len(parts) > 1 else ""
        return emojis, overlay

    return [], text
