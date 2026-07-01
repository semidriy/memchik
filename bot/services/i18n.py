"""i18n service: per-user language detection + string lookup with RU fallback.

Usage:
    from bot.services.i18n import t, user_lang
    lang = await user_lang(user_id)
    text = await t("template_added", lang)

Cache (user_id -> lang) has TTL of ~60s so language switches take effect quickly.
Hardcoded UI strings live in i18n_defaults.DEFAULTS; on startup, seed_defaults()
inserts missing RU rows into the i18n_strings table.
"""
import time
from bot.database import get_pool
from bot.database.queries import (
    get_user_lang as _db_get_user_lang,
    get_i18n_string as _db_get_string,
    seed_i18n_defaults as _db_seed,
)
from bot.services.i18n_defaults import DEFAULTS, DEFAULTS_BY_LANG

_TTL = 60.0
_lang_cache: dict[int, tuple[str, float]] = {}


def invalidate_lang_cache(user_id: int) -> None:
    _lang_cache.pop(user_id, None)


async def user_lang(user_id: int) -> str:
    now = time.monotonic()
    hit = _lang_cache.get(user_id)
    if hit and now - hit[1] < _TTL:
        return hit[0]
    lang = await _db_get_user_lang(get_pool(), user_id)
    _lang_cache[user_id] = (lang, now)
    return lang


async def t(key: str, lang: str = "ru", **kwargs) -> str:
    """Look up string for given key+lang. Falls back to a DB row, then to the shipped
    defaults for that language (English etc.), then RU, then the key itself."""
    val = await _db_get_string(get_pool(), key, lang)
    if val is None:
        val = DEFAULTS_BY_LANG.get(lang, {}).get(key) or DEFAULTS.get(key, key)
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return val
    return val


async def seed_defaults() -> None:
    """Run once at startup to populate any missing rows for every shipped language."""
    await _db_seed(get_pool(), DEFAULTS_BY_LANG)
