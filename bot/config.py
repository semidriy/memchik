from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator

ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    bot_token: str
    admin_ids: list[int]
    database_url: str
    # Единственный технический кэш-канал: media_server/meme.py заливают сюда каждую
    # уникальную пару (шаблон, текст) ОДИН раз, чтобы получить вечный file_id (см.
    # gif_cache). Поэтому «каждый закидывается» — это by design, а не баг.
    cache_chat_id: int
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

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        # Лишние/старые переменные в .env (напр. CACHE_CHAT_IDS) НЕ должны ронять
        # бота на старте — просто игнорируем неизвестные ключи.
        extra = "ignore"


settings = Settings()
