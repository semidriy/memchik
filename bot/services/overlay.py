import glob as _glob
import io
import logging
import os
import random
import subprocess
import tempfile
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _ffmpeg_path() -> str | None:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3)
        if r.returncode == 0:
            logger.info("ffmpeg: using PATH binary")
            return "ffmpeg"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(path):
            logger.info("ffmpeg: using imageio-ffmpeg bundled at %s", path)
            return path
    except Exception as e:
        logger.warning("imageio_ffmpeg lookup failed: %s", e)
    logger.warning("ffmpeg not found anywhere")
    return None


def _ffmpeg_available() -> bool:
    return _ffmpeg_path() is not None


@lru_cache(maxsize=1)
def _ffprobe_available() -> bool:
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=3)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# Шрифт ОБЯЗАН покрывать эти глифы — латиница, кириллица, цифры и базовая
# пунктуация ВКЛЮЧАЯ «?». Раньше требовалось покрытие и редких «»—…, из-за чего
# хорошие шрифты отбраковывались, а в фолбэк попадал случайный шрифт без глифа «?»
# (буквы рисуются, а «?» пропадает). Теперь шрифт без «?» не выберется никогда.
_ESSENTIAL_CHARS = "ABCXYZabcxyzАБВЭЮЯабвэюя0123456789?!.,-()"
# Желательные — предпочитаем шрифт, который покрывает и их, но не требуем.
_NICE_CHARS = _ESSENTIAL_CHARS + "«»—…:;\""


def _font_covers(path: str, chars: str):
    """True — покрывает все `chars`; False — не покрывает; None — проверить нельзя
    (нет fontTools / битый файл)."""
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(path)
        try:
            covered = set()
            for cmap in tt["cmap"].tables:
                covered.update(cmap.cmap.keys())
            return all(ord(ch) in covered for ch in chars)
        finally:
            tt.close()
    except Exception:
        return None


@lru_cache(maxsize=1)
def _find_font_path() -> str:
    # DejaVu Sans Bold first — covers full Cyrillic + punctuation reliably.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/local/share/fonts/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "C:/Windows/Fonts/impact.ttf",
    ]
    best = ""      # покрывает _NICE_CHARS
    ok = ""        # покрывает _ESSENTIAL_CHARS (в т.ч. «?»)
    unknown = ""   # существует, но проверить покрытие не удалось

    def consider(path: str) -> bool:
        """Учитываем кандидата. Возвращает True, если найден идеальный (best)."""
        nonlocal best, ok, unknown
        ess = _font_covers(path, _ESSENTIAL_CHARS)
        if ess is True:
            if not ok:
                ok = path
            if not best and _font_covers(path, _NICE_CHARS) is True:
                best = path
                return True
        elif ess is None and not unknown:
            unknown = path
        return False

    for path in candidates:
        if os.path.exists(path) and consider(path):
            logger.info("font: using %s", best)
            return best

    for pattern in (
        "/usr/share/fonts/**/*[Bb]old*.ttf",
        "/usr/share/fonts/**/*.ttf",
        "/usr/local/share/fonts/**/*.ttf",
    ):
        for m in _glob.glob(pattern, recursive=True):
            if consider(m):
                logger.info("font: found via glob %s", best)
                return best

    chosen = best or ok or unknown
    if chosen:
        logger.info("font: using %s (full=%s essential=%s)", chosen, bool(best), bool(ok))
        return chosen
    logger.warning("font: no TTF font found, text may not render correctly")
    return ""


def parse_overlay_text(text: str) -> tuple[str, str]:
    text = text.strip()
    if "." in text:
        top, _, bottom = text.partition(".")
        return top.strip().upper(), bottom.strip().upper()
    return text.upper(), ""


def _get_pil_font(size: int):
    fp = _find_font_path()
    if fp:
        try:
            return ImageFont.truetype(fp, size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()


def _word_width(font, s: str) -> int:
    try:
        return int(font.getlength(s))
    except AttributeError:
        pass
    try:
        bbox = font.getbbox(s)
        return bbox[2] - bbox[0]
    except (AttributeError, TypeError):
        return len(s) * (font.size if hasattr(font, "size") else 8)


def _split_long_token(word: str, font, max_width: int) -> list[str]:
    """Break a single oversized token by characters."""
    if _word_width(font, word) <= max_width:
        return [word]
    parts, current = [], ""
    for ch in word:
        test = current + ch
        if _word_width(font, test) <= max_width:
            current = test
        else:
            if current:
                parts.append(current)
            current = ch
    if current:
        parts.append(current)
    return parts or [word]


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        chunks = _split_long_token(word, font, max_width)
        for chunk in chunks:
            test = f"{current} {chunk}".strip()
            if _word_width(font, test) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = chunk
    if current:
        lines.append(current)
    return lines or [""]


def _draw_outlined(draw: ImageDraw.ImageDraw, xy: tuple, text: str, font, stroke: int = 5):
    x, y = xy
    draw.text((x, y), text, font=font, fill="white", anchor="mm",
              stroke_width=stroke, stroke_fill="black")


@lru_cache(maxsize=256)
def _cached_text_png(top: str, bottom: str, width: int, height: int) -> bytes:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Proportional to longest side so text looks the same SIZE relative to the gif.
    # 480px gif → 36px font; smaller gifs get proportionally smaller font.
    font_size = max(20, max(width, height) * 36 // 480)
    font = _get_pil_font(font_size)
    line_h = int(font_size * 1.2)
    stroke = max(2, font_size // 16)
    margin = max(10, int(height * 0.04))

    if top:
        lines = _wrap_text(top, font, int(width * 0.9))
        y0 = margin + font_size // 2
        for i, line in enumerate(lines):
            y = y0 + i * line_h
            if y - font_size // 2 < height // 2:
                _draw_outlined(draw, (width // 2, y), line, font, stroke)

    if bottom:
        lines = _wrap_text(bottom, font, int(width * 0.9))
        y_last = height - margin - font_size // 2
        y0 = y_last - (len(lines) - 1) * line_h
        for i, line in enumerate(lines):
            y = y0 + i * line_h
            if y + font_size // 2 > height // 2:
                _draw_outlined(draw, (width // 2, y), line, font, stroke)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=False)
    return out.getvalue()


def _probe_dimensions(data: bytes, is_gif: bool, is_mp4: bool) -> tuple[int, int]:
    if is_gif:
        try:
            img = Image.open(io.BytesIO(data))
            return img.size
        except Exception:
            pass
    if is_mp4:
        tmp_path = None
        try:
            tmp_path = _write_tmp(data, ".mp4")
            import imageio
            reader = imageio.get_reader(tmp_path, format="ffmpeg")
            meta = reader.get_meta_data()
            reader.close()
            size = meta.get("size") or meta.get("source_size")
            if size and len(size) == 2:
                return int(size[0]), int(size[1])
        except Exception as e:
            logger.warning("imageio probe failed: %s", e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    try:
        img = Image.open(io.BytesIO(data))
        return img.size
    except Exception:
        return 480, 270


def _write_tmp(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def _ffmpeg_overlay_gif(data: bytes, png_bytes: bytes) -> bytes | None:
    in_tmp = png_tmp = out_tmp = None
    try:
        in_tmp = _write_tmp(data, ".gif")
        png_tmp = _write_tmp(png_bytes, ".png")
        out_fd, out_tmp = tempfile.mkstemp(suffix=".gif")
        os.close(out_fd)

        r = subprocess.run(
            [_ffmpeg_path(), "-y", "-i", in_tmp, "-i", png_tmp,
             "-filter_complex", "[0:v][1:v]overlay=0:0",
             out_tmp],
            capture_output=True, timeout=15,
        )
        if r.returncode != 0:
            logger.warning("ffmpeg gif failed: %s", r.stderr.decode(errors="replace")[:300])
            return None
        with open(out_tmp, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning("ffmpeg gif exception: %s", e)
        return None
    finally:
        for p in (in_tmp, png_tmp, out_tmp):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


# Higher quality — output goes via file_id (cache_chat), не упирается в 1MB URL-лимит.
_CODEC_CANDIDATES = (
    ("libx264", ["-preset", "veryfast", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2"]),
    ("mpeg4", ["-q:v", "3", "-pix_fmt", "yuv420p", "-threads", "2"]),
)
_OUT_MAX_DIM = 720
_OUT_FPS = 30


def _ffmpeg_overlay_mp4(data: bytes, png_bytes: bytes, out_w: int, out_h: int) -> bytes | None:
    in_tmp = png_tmp = None
    try:
        in_tmp = _write_tmp(data, ".mp4")
        png_tmp = _write_tmp(png_bytes, ".png")

        for codec, extra_args in _CODEC_CANDIDATES:
            out_fd, out_tmp = tempfile.mkstemp(suffix=".mp4")
            os.close(out_fd)
            try:
                # Scale video to target size; PNG was already rendered at that size.
                cmd = [
                    _ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", in_tmp, "-i", png_tmp,
                    "-filter_complex",
                    f"[0:v]fps={_OUT_FPS},scale={out_w}:{out_h},format=yuv420p[v0];[v0][1:v]overlay=0:0",
                    "-c:v", codec, *extra_args, "-an", out_tmp,
                ]
                r = subprocess.run(cmd, capture_output=True, timeout=15)
                if r.returncode != 0:
                    logger.warning("ffmpeg mp4 codec=%s failed: %s", codec,
                                   r.stderr.decode(errors="replace")[:300])
                    continue
                with open(out_tmp, "rb") as f:
                    return f.read()
            except Exception as e:
                logger.warning("ffmpeg mp4 codec=%s exception: %s", codec, e)
            finally:
                if os.path.exists(out_tmp):
                    try:
                        os.unlink(out_tmp)
                    except OSError:
                        pass
        logger.warning("ffmpeg mp4: all codecs failed")
        return None
    except Exception as e:
        logger.warning("ffmpeg mp4 outer exception: %s", e)
        return None
    finally:
        for p in (in_tmp, png_tmp):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _pil_overlay_image(img_bytes: bytes, png_bytes: bytes) -> bytes:
    base = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    overlay = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    # PNG was rendered at the SCALED output size; resize the base to match,
    # otherwise the text lands in the top-left corner of the original image.
    if base.size != overlay.size:
        base = base.resize(overlay.size, Image.LANCZOS)
    base.alpha_composite(overlay)
    out = io.BytesIO()
    base.convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()


def _pil_overlay_gif(gif_bytes: bytes, png_bytes: bytes) -> bytes:
    gif = Image.open(io.BytesIO(gif_bytes))
    overlay = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    target = overlay.size
    frames, durations = [], []
    try:
        while True:
            frame = gif.copy().convert("RGBA")
            if frame.size != target:
                frame = frame.resize(target, Image.LANCZOS)
            frame.alpha_composite(overlay)
            frames.append(frame)
            durations.append(gif.info.get("duration", 100))
            gif.seek(gif.tell() + 1)
    except EOFError:
        pass

    if not frames:
        return gif_bytes

    rgb_frames = []
    for f in frames:
        bg = Image.new("RGB", f.size, (0, 0, 0))
        bg.paste(f, mask=f.split()[3])
        rgb_frames.append(bg)

    out = io.BytesIO()
    rgb_frames[0].save(
        out, format="GIF", save_all=True,
        append_images=rgb_frames[1:], loop=0,
        duration=durations, optimize=False,
    )
    return out.getvalue()


def _scale_for_output(w: int, h: int) -> tuple[int, int]:
    """Cap longest side at _OUT_MAX_DIM, keep aspect, force even dims (required by yuv420p)."""
    longest = max(w, h)
    if longest <= _OUT_MAX_DIM:
        return (w // 2) * 2, (h // 2) * 2
    scale = _OUT_MAX_DIM / longest
    return (int(w * scale) // 2) * 2, (int(h * scale) // 2) * 2


def _first_frame_jpeg(data: bytes) -> bytes | None:
    """Extract first frame of an mp4 as JPEG via ffmpeg."""
    ff = _ffmpeg_path()
    if not ff:
        return None
    in_tmp = out_tmp = None
    try:
        in_tmp = _write_tmp(data, ".mp4")
        out_fd, out_tmp = tempfile.mkstemp(suffix=".jpg")
        os.close(out_fd)
        r = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-i", in_tmp, "-vframes", "1", out_tmp],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0 or not os.path.exists(out_tmp):
            return None
        with open(out_tmp, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning("first-frame ffmpeg error: %s", e)
        return None
    finally:
        for p in (in_tmp, out_tmp):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def render_static_preview(data: bytes, text: str, base_frame: bytes | None = None) -> bytes:
    """First frame (gif/mp4) or the photo itself + text → JPEG.
    Cheap single-frame composite, no video re-encode. Used for inline previews.

    `base_frame` (optional): a pre-extracted first-frame JPEG. The first frame of a
    template is identical for every overlay text, so the caller caches it once per
    template and passes it here — this skips the per-keystroke ffmpeg extraction for
    mp4 templates, which was the main cause of slow previews while typing."""
    top, bottom = parse_overlay_text(text)
    is_gif = data[:3] == b"GIF"
    is_mp4 = len(data) >= 8 and data[4:8] == b"ftyp"

    frame = None
    if base_frame:
        try:
            frame = Image.open(io.BytesIO(base_frame))
        except Exception:
            frame = None
    if frame is None and is_mp4:
        jpg = _first_frame_jpeg(data)
        if jpg:
            frame = Image.open(io.BytesIO(jpg))
    if frame is None:
        frame = Image.open(io.BytesIO(data))
        try:
            frame.seek(0)
        except Exception:
            pass
    frame = frame.convert("RGBA")

    w, h = frame.size
    out_w, out_h = _scale_for_output(w, h)
    if (out_w, out_h) != (w, h):
        frame = frame.resize((out_w, out_h))

    if top or bottom:
        png_bytes = _cached_text_png(top, bottom, out_w, out_h)
        overlay = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        frame.alpha_composite(overlay)

    # Для превью режем размер агрессивнее (512px вместо 720) — превью не
    # должен быть Hi-Res, главное чтоб в инлайне быстро прилетел целым.
    PREVIEW_MAX = 512
    longest = max(frame.size)
    if longest > PREVIEW_MAX:
        scale = PREVIEW_MAX / longest
        nw = (int(frame.size[0] * scale) // 2) * 2
        nh = (int(frame.size[1] * scale) // 2) * 2
        frame = frame.resize((nw, nh))

    rgb = frame.convert("RGB")
    # Невидимая модификация одного пикселя в углу → байты каждый раз новые →
    # другой file_unique_id у Telegram → TG не отдаёт кэшированный битый вариант.
    px = rgb.load()
    px[rgb.size[0] - 1, rgb.size[1] - 1] = (
        random.randint(0, 255), random.randint(0, 255), random.randint(0, 255),
    )
    out = io.BytesIO()
    # Baseline JPEG, 4:2:0 — самый совместимый формат. Низкое quality чтобы
    # файл был МАЛЕНЬКИЙ: похоже что TG-фетчер где-то жёстко режет по
    # таймауту/размеру, и тогда мы упираемся в серый низ. Чем меньше байтов —
    # тем выше шанс что они доедут целиком до клиента.
    rgb.save(
        out, format="JPEG",
        quality=random.randint(78, 84),
        optimize=True,
        progressive=False,
        subsampling=2,   # 4:2:0
    )
    return out.getvalue()


def add_text_auto(data: bytes, text: str) -> tuple[bytes, bool]:
    """Returns (processed_bytes, is_animation)."""
    top, bottom = parse_overlay_text(text)
    if not top and not bottom:
        return data, data[:3] == b"GIF" or (len(data) >= 8 and data[4:8] == b"ftyp")

    is_gif = data[:3] == b"GIF"
    is_mp4 = len(data) >= 8 and data[4:8] == b"ftyp"

    src_w, src_h = _probe_dimensions(data, is_gif, is_mp4)
    out_w, out_h = _scale_for_output(src_w, src_h)
    png_bytes = _cached_text_png(top, bottom, out_w, out_h)

    if _ffmpeg_available():
        if is_gif or is_mp4:
            result = _ffmpeg_overlay_mp4(data, png_bytes, out_w, out_h)
            if result:
                return result, True
            if is_gif:
                result = _ffmpeg_overlay_gif(data, png_bytes)
                if result:
                    return result, True

    if is_gif:
        return _pil_overlay_gif(data, png_bytes), True
    if is_mp4:
        logger.warning("ffmpeg failed for mp4, no PIL fallback; returning original")
        return data, True
    return _pil_overlay_image(data, png_bytes), False
