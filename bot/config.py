import hashlib
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator

ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    bot_token: str
    admin_ids: list[int]
    database_url: str
    # Технический кэш-канал: meme.py / media_server заливают сюда каждую уникальную пару
    # (шаблон, текст) ОДИН раз, чтобы получить вечный file_id (см. gif_cache). Поэтому
    # «каждый закидывается» — это by design, а не баг.
    cache_chat_id: int
    # Доп. кэш-каналы (CACHE_CHAT_IDS): заливки раскидываются по всем каналам
    # (primary + эти), чтобы на пике не упереться в per-chat флуд-лимит Телеграма.
    # Какой канал ни выбери — file_id всё равно глобален для бота, на выдачу не влияет.
    # Бот должен быть админом с правом постить в КАЖДОМ из них.
    cache_chat_ids: list[int] = []
    # Остальные каналы — это НЕ кэш, а пайплайн шаблонов:
    moderation_channel_id: int | None = None   # публичные шаблоны — очередь модерации
    personal_channel_id: int | None = None     # личные (быстрые) шаблоны — лог с удалением
    public_channel_id: int | None = None       # витрина одобренных шаблонов

    # Media server
    media_server_url: str = ""        # e.g. https://tgp.tgis.vu
    media_server_secret: str = ""     # HMAC secret shared between bot and media server

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, (int, float)):
            return [int(v)]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("cache_chat_id", mode="before")
    @classmethod
    def parse_first_int(cls, v):
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            # Strip JSON-array brackets, take first element of comma-separated list
            v = v.strip().strip("[]")
            return int(v.split(",")[0].strip())
        return v

    @field_validator("cache_chat_ids", mode="before")
    @classmethod
    def parse_cache_ids(cls, v):
        # Принимаем "[-100..,-100..]" (JSON-массив как строка), "-100..,-100.." или список.
        if v is None or v == "":
            return []
        if isinstance(v, (int, float)):
            return [int(v)]
        if isinstance(v, str):
            v = v.strip().strip("[]")
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        return v

    @property
    def cache_targets(self) -> list[int]:
        """Все каналы для заливки кэша: primary всегда первым, дубликаты убраны."""
        seen: dict[int, None] = {}
        for cid in [self.cache_chat_id, *self.cache_chat_ids]:
            seen[cid] = None
        return list(seen)

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        # Лишние/старые переменные в .env (напр. CACHE_CHAT_IDS) НЕ должны ронять
        # бота на старте — просто игнорируем неизвестные ключи.
        extra = "ignore"


settings = Settings()


def pick_cache_chat(key: str) -> int:
    """Выбрать кэш-канал для этой заливки, детерминированно по ключу (обычно
    f"{template_id}:{text_hash}"). Хэш стабилен между процессами (md5, не salted
    hash()), поэтому бот и медиасервер выберут одинаково и нагрузка ровно
    размажется по всем cache_targets. Один канал → он и вернётся."""
    targets = settings.cache_targets
    if len(targets) <= 1:
        return targets[0]
    idx = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(targets)
    return targets[idx]
