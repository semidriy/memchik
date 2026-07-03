import io

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.config import settings, pick_cache_chat


async def video_to_animation(bot: Bot, file_id: str) -> tuple[str, str]:
    """Телеграм присылает обычный MP4 (из галереи / со звуком) как message.video,
    а НЕ animation, и старый код такие сообщения молча отбрасывал → «видео не
    добавляется». Перезаливаем байты как animation в кэш-чат: получаем валидный
    animation file_id (звук Телеграм отбрасывает → зацикленная гифка). Такой file_id
    корректно работает во ВСЕХ путях (inline CachedMpeg4Gif без текста, оверлей текста
    через медиасервер и т.д.) — тот же приём, что в meme._upload_and_cache. Сырой
    video file_id, сохранённый как "animation", сломал бы inline-выдачу без текста.

    Возвращает (file_id, file_unique_id) уже готовой анимации.
    """
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    data = buf.getvalue()

    # Выбранный канал, с откатом на primary (вдруг бот не админ в доп. канале).
    primary = settings.cache_chat_id
    picked = pick_cache_chat(file_id)
    targets = [picked] if picked == primary else [picked, primary]
    last_err: Exception | None = None
    for target in targets:
        try:
            sent = await bot.send_animation(
                target, BufferedInputFile(data, filename="template.mp4"),
            )
            if sent.animation:
                return sent.animation.file_id, sent.animation.file_unique_id
            # Подстраховка: если Телеграм всё же вернул видео (редко) — берём его file_id.
            if sent.video:
                return sent.video.file_id, sent.video.file_unique_id
            raise RuntimeError("send_animation вернул сообщение без animation/video")
        except Exception as e:
            last_err = e
    raise last_err if last_err else RuntimeError("cache upload failed")
