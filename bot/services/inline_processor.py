import hashlib
import hmac
import urllib.parse

def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]

from bot.config import settings

# Cache-buster для URL превью. Бампать при любых правках в render_static_preview /
# media_server, чтобы Telegram-клиенты перекачали (старый URL остаётся в их
# локальном кэше до суток). При новом значении — старые битые превью игнорятся.
PREVIEW_URL_VERSION = "7"


def _sign(template_id: int, text: str) -> str:
    if not settings.media_server_secret:
        return ""
    return hmac.new(
        settings.media_server_secret.encode(),
        f"{template_id}:{text}".encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


def build_media_url(template_id: int, text: str = "", ext: str = "mp4") -> str:
    """Telegram inline result URLs must end with proper media extension (.mp4/.jpg)
    or Telegram rejects them silently. The ext is part of the URL path."""
    base = settings.media_server_url.rstrip("/")
    params: dict[str, str] = {}
    if text:
        params["text"] = text
    sig = _sign(template_id, text)
    if sig:
        params["sig"] = sig
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    return f"{base}/media/{template_id}.{ext}{qs}"


def build_preview_url(template_id: int, text: str = "") -> str:
    """Static JPEG preview (first frame + text). Telegram fetches it for inline photo results.
    Text hash is embedded in the URL path so mobile TG's path-based photo cache stays unique
    per text value (query-param-only changes are ignored by some clients)."""
    base = settings.media_server_url.rstrip("/")
    th = _text_hash(text) if text else "0"
    params: dict[str, str] = {"v": PREVIEW_URL_VERSION}
    if text:
        params["text"] = text
    sig = _sign(template_id, text)
    if sig:
        params["sig"] = sig
    return f"{base}/preview/{template_id}/{th}.jpg?{urllib.parse.urlencode(params)}"


def build_thumb_url(template_id: int) -> str:
    base = settings.media_server_url.rstrip("/")
    params: dict[str, str] = {"v": PREVIEW_URL_VERSION}
    sig = _sign(template_id, "")
    if sig:
        params["sig"] = sig
    return f"{base}/thumb/{template_id}.jpg?{urllib.parse.urlencode(params)}"


async def prewarm_templates(_bot):
    """No-op — media server warms its own disk cache on first request."""
    pass
