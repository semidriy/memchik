import io

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.config import settings


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
    sent = await bot.send_animation(
        settings.cache_chat_id,
        BufferedInputFile(buf.getvalue(), filename="template.mp4"),
    )
    if sent.animation:
        return sent.animation.file_id, sent.animation.file_unique_id
    # Подстраховка: если Телеграм всё же вернул видео (редко) — берём его file_id.
    if sent.video:
        return sent.video.file_id, sent.video.file_unique_id
    raise RuntimeError("send_animation вернул сообщение без animation/video")
