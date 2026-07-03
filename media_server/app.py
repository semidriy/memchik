import asyncio
import hashlib
import hmac
import io
import logging
import os
import subprocess
import tempfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from PIL import Image

from bot.config import settings, pick_cache_chat
from bot.services.overlay import add_text_auto, render_static_preview, _ffmpeg_path, _first_frame_jpeg

logger = logging.getLogger(__name__)

CACHE_DIR = Path("media_cache")
RAW_DIR = CACHE_DIR / "raw"
PROC_DIR = CACHE_DIR / "proc"
THUMB_DIR = CACHE_DIR / "thumb"

_pool: asyncpg.Pool | None = None
_http: httpx.AsyncClient | None = None

# In-memory LRU: (template_id, text_hash) -> (bytes, content_type)
_mem: OrderedDict[tuple, tuple] = OrderedDict()
_MEM_MAX = 300

# Per-key lock to avoid duplicate processing under concurrent requests
_locks: dict[tuple, asyncio.Lock] = {}

# Limit concurrent ffmpeg processes. Set high so a single inline query (~20 templates)
# completes in one wave instead of batching serially.
_FFMPEG_SEM = asyncio.Semaphore(16)


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

def _sign(template_id: int, text: str) -> str:
    if not settings.media_server_secret:
        return ""
    return hmac.new(
        settings.media_server_secret.encode(),
        f"{template_id}:{text}".encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


def _verify(template_id: int, text: str, sig: str) -> bool:
    if not settings.media_server_secret:
        return True
    return hmac.compare_digest(_sign(template_id, text), sig)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _pool, _http
    for d in (CACHE_DIR, RAW_DIR, PROC_DIR, THUMB_DIR):
        d.mkdir(parents=True, exist_ok=True)
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    _http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    logger.info("Media server ready")
    yield
    await _pool.close()
    await _http.aclose()


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_ext(data: bytes) -> str:
    if data[:3] == b"GIF":
        return "gif"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "mp4"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "bin"


async def _fetch_template(template_id: int) -> dict | None:
    row = await _pool.fetchrow(
        "SELECT id, file_id, file_type FROM templates WHERE id = $1 AND is_active = TRUE",
        template_id,
    )
    return dict(row) if row else None


def _validate_media_bytes(data: bytes, ext: str) -> bool:
    """Проверяем что байты не обрезаны. Для JPEG/PNG/GIF — по магии конца файла.
    Для MP4 проверяем что есть и ftyp, и moov-атом ближе к концу."""
    if not data or len(data) < 8:
        return False
    if ext in ("jpg", "jpeg"):
        # JPEG обязан заканчиваться на 0xFFD9 (EOI marker)
        return data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"
    if ext == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n" and data[-12:].endswith(b"IEND\xaeB`\x82")
    if ext == "gif":
        return data[:3] == b"GIF" and data[-1:] == b";"
    if ext == "mp4":
        # moov-атом должен присутствовать (без него видео не воспроизведётся)
        return b"moov" in data and len(data) >= 1024
    return True


async def _download_from_telegram(file_id: str) -> bytes:
    """Качаем шаблон, **строго** проверяя что доехало целиком.
    httpx может молча отдать partial content при разрыве соединения, а PIL потом
    «дорисует» серым пропавшую часть JPEG — отсюда половина превью серая.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = await _http.get(
                f"https://api.telegram.org/bot{settings.bot_token}/getFile",
                params={"file_id": file_id},
            )
            r.raise_for_status()
            file_path = r.json()["result"]["file_path"]
            dl = await _http.get(
                f"https://api.telegram.org/file/bot{settings.bot_token}/{file_path}"
            )
            dl.raise_for_status()
            data = dl.content

            expected = int(dl.headers.get("Content-Length", 0) or 0)
            if expected and len(data) != expected:
                raise IOError(
                    f"truncated tg download: got {len(data)}/{expected} for {file_id}"
                )

            ext = _detect_ext(data)
            if not _validate_media_bytes(data, ext):
                raise IOError(
                    f"corrupt tg download: ext={ext} size={len(data)} for {file_id}"
                )
            return data
        except Exception as e:
            last_err = e
            logger.warning("tg download attempt %s failed: %s", attempt + 1, e)
            await asyncio.sleep(0.5 * (attempt + 1))
    raise IOError(f"tg download failed after retries: {last_err}")


async def _get_raw(template_id: int, file_id: str) -> bytes:
    """Return raw template bytes. Если на диске уже есть, но он битый
    (обрезан) — выбрасываем и качаем заново. Иначе PIL «дорисует» серым
    недостающие байты JPEG, и превью получится с серой нижней половиной.
    """
    for ext in ("mp4", "gif", "jpg", "jpeg", "png", "bin"):
        p = RAW_DIR / f"{template_id}.{ext}"
        if p.exists():
            data = p.read_bytes()
            check_ext = "jpg" if ext == "jpeg" else ext
            if _validate_media_bytes(data, check_ext):
                return data
            logger.warning("raw %s for tpl=%s is corrupt (%d bytes), re-downloading",
                           ext, template_id, len(data))
            try:
                p.unlink()
            except OSError:
                pass
            break
    data = await _download_from_telegram(file_id)
    ext = _detect_ext(data)
    _atomic_write(RAW_DIR / f"{template_id}.{ext}", data)
    return data


def _make_thumb_sync(data: bytes) -> bytes:
    # Try PIL (works for GIF, JPEG, PNG)
    try:
        img = Image.open(io.BytesIO(data))
        try:
            img.seek(0)
        except Exception:
            pass
        img = img.convert("RGB")
        img.thumbnail((320, 240))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=75)
        return out.getvalue()
    except Exception:
        pass

    # For MP4: extract first frame via ffmpeg
    ffmpeg = _ffmpeg_path()
    if ffmpeg:
        tmp_in = tmp_out = None
        try:
            fd, tmp_in = tempfile.mkstemp(suffix=".mp4")
            os.write(fd, data)
            os.close(fd)
            fd, tmp_out = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            subprocess.run(
                [ffmpeg, "-y", "-i", tmp_in, "-vframes", "1",
                 "-vf", "scale=320:-1", tmp_out],
                capture_output=True, timeout=10,
            )
            with open(tmp_out, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning("thumb ffmpeg error: %s", e)
        finally:
            for p in (tmp_in, tmp_out):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    # Last resort: 1×1 gray pixel
    img = Image.new("RGB", (1, 1), (100, 100, 100))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


def _mem_put(key: tuple, val: tuple):
    _mem[key] = val
    _mem.move_to_end(key)
    while len(_mem) > _MEM_MAX:
        _mem.popitem(last=False)


def _atomic_write(path: Path, data: bytes) -> None:
    """Запись через .tmp + os.replace, чтобы конкурентные читатели никогда не
    видели наполовину записанный файл (читали бы партиал → битый JPEG)."""
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_read(path: Path) -> bytes | None:
    """Читаем целиком. Если файл вдруг неконсистентный (пустой) — None."""
    try:
        data = path.read_bytes()
        return data if data else None
    except FileNotFoundError:
        return None


def _get_lock(key: tuple) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"ok": True}


@app.head("/media/{template_id}")
@app.head("/media/{template_id}.{ext}")
async def head_media(
    template_id: int,
    text: str = Query(default=""),
    sig: str = Query(default=""),
    ext: str = "mp4",
):
    if not _verify(template_id, text, sig):
        raise HTTPException(status_code=403)
    text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
    mem_key = (template_id, text_hash)
    if mem_key in _mem:
        _, ct = _mem[mem_key]
        return Response(media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    for ext, ct in (("mp4", "video/mp4"), ("gif", "image/gif"), ("jpg", "image/jpeg")):
        if (PROC_DIR / f"{template_id}_{text_hash}.{ext}").exists():
            return Response(media_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    template = await _fetch_template(template_id)
    if not template:
        raise HTTPException(status_code=404)
    return Response(media_type="video/mp4", headers={"Cache-Control": "public, max-age=86400"})


@app.head("/preview/{template_id}")
@app.head("/preview/{template_id}.jpg")
@app.head("/preview/{template_id}/{text_hash}.jpg")
async def head_preview(
    template_id: int,
    text: str = Query(default=""),
    sig: str = Query(default=""),
    text_hash: str = "",
):
    if not _verify(template_id, text, sig):
        raise HTTPException(status_code=403)
    return Response(media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/preview/{template_id}")
@app.get("/preview/{template_id}.jpg")
@app.get("/preview/{template_id}/{text_hash}.jpg")
async def serve_preview(
    template_id: int,
    text: str = Query(default=""),
    sig: str = Query(default=""),
    text_hash: str = "",
):
    """Static JPEG (first frame + text) — cheap, no video encode. For inline previews."""
    if not _verify(template_id, text, sig):
        logger.warning("preview sig fail tpl=%s text=%r", template_id, text)
        raise HTTPException(status_code=403)

    text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
    logger.info("preview tpl=%s text=%r hash=%s path_hash=%r", template_id, text, text_hash, text_hash)
    p = PROC_DIR / f"{template_id}_{text_hash}_p.jpg"
    cached = _safe_read(p)
    if cached:
        return Response(content=cached, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

    async with _get_lock(("preview", template_id, text_hash)):
        cached = _safe_read(p)
        if cached:
            return Response(content=cached, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

        template = await _fetch_template(template_id)
        if not template:
            raise HTTPException(status_code=404)
        raw = await _get_raw(template_id, template["file_id"])

        # The first frame is text-independent, so extract it once per template and
        # reuse it for every overlay text. Without this each new text typed re-ran an
        # ffmpeg first-frame extraction per template (≈50/keystroke) → slow previews.
        loop = asyncio.get_event_loop()
        base_frame = None
        is_mp4 = len(raw) >= 8 and raw[4:8] == b"ftyp"
        if is_mp4:
            bf_path = PROC_DIR / f"{template_id}_baseframe.jpg"
            base_frame = _safe_read(bf_path)
            if not base_frame:
                async with _FFMPEG_SEM:
                    base_frame = await loop.run_in_executor(None, _first_frame_jpeg, raw)
                if base_frame:
                    _atomic_write(bf_path, base_frame)

        # With base_frame in hand (or for gif/photo) the composite is pure PIL — no
        # ffmpeg — so it doesn't need the ffmpeg semaphore and can run concurrently.
        data = await loop.run_in_executor(
            None, render_static_preview, raw, text, base_frame
        )
        _atomic_write(p, data)
        _locks.pop(("preview", template_id, text_hash), None)

    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.head("/thumb/{template_id}")
@app.head("/thumb/{template_id}.jpg")
async def head_thumb(
    template_id: int,
    sig: str = Query(default=""),
):
    if not _verify(template_id, "", sig):
        raise HTTPException(status_code=403)
    if not (THUMB_DIR / f"{template_id}.jpg").exists():
        template = await _fetch_template(template_id)
        if not template:
            raise HTTPException(status_code=404)
    return Response(media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/media/{template_id}")
@app.get("/media/{template_id}.{ext}")
async def serve_media(
    template_id: int,
    text: str = Query(default=""),
    sig: str = Query(default=""),
    ext: str = "mp4",
):
    if not _verify(template_id, text, sig):
        raise HTTPException(status_code=403)

    text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
    mem_key = (template_id, text_hash)

    if mem_key in _mem:
        data, ct = _mem[mem_key]
        _mem.move_to_end(mem_key)
        return Response(content=data, media_type=ct,
                        headers={"Cache-Control": "public, max-age=86400"})

    # Check disk cache
    for ext, ct in (("mp4", "video/mp4"), ("gif", "image/gif"), ("jpg", "image/jpeg")):
        p = PROC_DIR / f"{template_id}_{text_hash}.{ext}"
        cached = _safe_read(p)
        if cached:
            _mem_put(mem_key, (cached, ct))
            return Response(content=cached, media_type=ct,
                            headers={"Cache-Control": "public, max-age=86400"})

    async with _get_lock(mem_key):
        if mem_key in _mem:
            data, ct = _mem[mem_key]
            return Response(content=data, media_type=ct,
                            headers={"Cache-Control": "public, max-age=86400"})
        for ext, ct in (("mp4", "video/mp4"), ("gif", "image/gif"), ("jpg", "image/jpeg")):
            p = PROC_DIR / f"{template_id}_{text_hash}.{ext}"
            cached = _safe_read(p)
            if cached:
                _mem_put(mem_key, (cached, ct))
                return Response(content=cached, media_type=ct,
                                headers={"Cache-Control": "public, max-age=86400"})

        template = await _fetch_template(template_id)
        if not template:
            raise HTTPException(status_code=404)

        raw = await _get_raw(template_id, template["file_id"])

        if text:
            async with _FFMPEG_SEM:
                data, is_anim = await asyncio.get_event_loop().run_in_executor(
                    None, add_text_auto, raw, text
                )
        else:
            data = raw
            is_anim = raw[:3] == b"GIF" or (len(raw) >= 8 and raw[4:8] == b"ftyp")

        if is_anim:
            if len(data) >= 8 and data[4:8] == b"ftyp":
                ext, ct = "mp4", "video/mp4"
            else:
                ext, ct = "gif", "image/gif"
        else:
            ext, ct = "jpg", "image/jpeg"

        _atomic_write(PROC_DIR / f"{template_id}_{text_hash}.{ext}", data)
        _mem_put(mem_key, (data, ct))
        _locks.pop(mem_key, None)

    return Response(content=data, media_type=ct,
                    headers={"Cache-Control": "public, max-age=86400"})


async def _get_processed(template_id: int, text: str) -> tuple[bytes, str, str, bool]:
    """Return (data, content_type, ext, is_animation). Uses caches; processes if needed."""
    text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
    mem_key = (template_id, text_hash)

    if mem_key in _mem:
        data, ct = _mem[mem_key]
        _mem.move_to_end(mem_key)
        ext = "mp4" if ct == "video/mp4" else "gif" if ct == "image/gif" else "jpg"
        return data, ct, ext, ct != "image/jpeg"

    for ext, ct in (("mp4", "video/mp4"), ("gif", "image/gif"), ("jpg", "image/jpeg")):
        p = PROC_DIR / f"{template_id}_{text_hash}.{ext}"
        cached = _safe_read(p)
        if cached:
            _mem_put(mem_key, (cached, ct))
            return cached, ct, ext, ct != "image/jpeg"

    async with _get_lock(mem_key):
        if mem_key in _mem:
            data, ct = _mem[mem_key]
            ext = "mp4" if ct == "video/mp4" else "gif" if ct == "image/gif" else "jpg"
            return data, ct, ext, ct != "image/jpeg"
        for ext, ct in (("mp4", "video/mp4"), ("gif", "image/gif"), ("jpg", "image/jpeg")):
            p = PROC_DIR / f"{template_id}_{text_hash}.{ext}"
            cached = _safe_read(p)
            if cached:
                _mem_put(mem_key, (cached, ct))
                return cached, ct, ext, ct != "image/jpeg"

        template = await _fetch_template(template_id)
        if not template:
            raise HTTPException(status_code=404)

        raw = await _get_raw(template_id, template["file_id"])

        if text:
            async with _FFMPEG_SEM:
                data, is_anim = await asyncio.get_event_loop().run_in_executor(
                    None, add_text_auto, raw, text
                )
        else:
            data = raw
            is_anim = raw[:3] == b"GIF" or (len(raw) >= 8 and raw[4:8] == b"ftyp")

        if is_anim:
            if len(data) >= 8 and data[4:8] == b"ftyp":
                ext, ct = "mp4", "video/mp4"
            else:
                ext, ct = "gif", "image/gif"
        else:
            ext, ct = "jpg", "image/jpeg"

        _atomic_write(PROC_DIR / f"{template_id}_{text_hash}.{ext}", data)
        _mem_put(mem_key, (data, ct))
        _locks.pop(mem_key, None)
        return data, ct, ext, is_anim


@app.post("/cache/{template_id}")
async def cache_to_telegram(
    template_id: int,
    text: str = Query(default=""),
    sig: str = Query(default=""),
):
    """Process media, upload to cache_chat via Bot API, store file_id in gif_cache, return it.
    Saves the bot the round-trip of downloading then re-uploading the bytes."""
    if not _verify(template_id, text, sig):
        raise HTTPException(status_code=403)

    text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
    gif_id_str = str(template_id)

    cached = await _pool.fetchval(
        "SELECT telegram_file_id FROM gif_cache WHERE gif_id = $1 AND text_hash = $2",
        gif_id_str, text_hash,
    )
    if cached:
        return {"file_id": cached, "cached": True}

    data, ct, ext, is_anim = await _get_processed(template_id, text)

    method = "sendAnimation" if is_anim else "sendPhoto"
    field = "animation" if is_anim else "photo"
    filename = f"out.{ext}"
    tg_url = f"https://api.telegram.org/bot{settings.bot_token}/{method}"

    async def _send(chat_id: int):
        # files пересобираем на каждую попытку — httpx «съедает» тело при отправке.
        return await _http.post(
            tg_url, data={"chat_id": str(chat_id)},
            files={field: (filename, data, ct)}, timeout=30.0,
        )

    # Раскидываем по каналам; при сбое доп. канала откатываемся на primary.
    primary = settings.cache_chat_id
    picked = pick_cache_chat(f"{template_id}:{text_hash}")
    r = await _send(picked)
    if r.status_code != 200 and picked != primary:
        logger.warning("telegram %s to %s failed (%s), retry primary",
                       method, picked, r.status_code)
        r = await _send(primary)
    if r.status_code != 200:
        logger.warning("telegram %s failed for tpl=%s: %s", method, template_id, r.text[:200])
        raise HTTPException(status_code=502, detail="telegram upload failed")

    result = r.json().get("result") or {}
    if is_anim:
        file_id = (result.get("animation") or {}).get("file_id")
    else:
        photos = result.get("photo") or []
        file_id = photos[-1].get("file_id") if photos else None
    if not file_id:
        raise HTTPException(status_code=502, detail="no file_id in telegram response")

    await _pool.execute(
        "INSERT INTO gif_cache (gif_id, text_hash, telegram_file_id) VALUES ($1, $2, $3) "
        "ON CONFLICT DO NOTHING",
        gif_id_str, text_hash, file_id,
    )
    return {"file_id": file_id, "cached": False}


@app.get("/thumb/{template_id}")
@app.get("/thumb/{template_id}.jpg")
async def serve_thumb(
    template_id: int,
    sig: str = Query(default=""),
):
    if not _verify(template_id, "", sig):
        raise HTTPException(status_code=403)

    thumb_path = THUMB_DIR / f"{template_id}.jpg"
    cached = _safe_read(thumb_path)
    if cached:
        return Response(content=cached, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})

    async with _get_lock(("thumb", template_id)):
        cached = _safe_read(thumb_path)
        if cached:
            return Response(content=cached, media_type="image/jpeg")

        template = await _fetch_template(template_id)
        if not template:
            raise HTTPException(status_code=404)

        raw = await _get_raw(template_id, template["file_id"])
        thumb = await asyncio.get_event_loop().run_in_executor(None, _make_thumb_sync, raw)
        _atomic_write(thumb_path, thumb)
        _locks.pop(("thumb", template_id), None)

    return Response(content=thumb, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"})
