from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator

ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    bot_token: str
    admin_ids: list[int]
    database_url: str
    cache_chat_id: int  # primary cache chat (for meme.py)
    cache_chat_ids: list[int] = []
    moderation_channel_id: int | None = None   # публичные шаблоны — модерация
    personal_channel_id: int | None = None     # личные (быстрые) шаблоны — только удалить
    public_channel_id: int | None = None

    # Media server
    media_server_url: str = ""        # e.g. https://tgp.tgis.vu
    media_server_secret: str = ""     # HMAC secret shared between bot and media server

    @field_validator("admin_ids", "cache_chat_ids", mode="before")
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

    @property
    def all_cache_chat_ids(self) -> list[int]:
        return self.cache_chat_ids or [self.cache_chat_id]

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"


settings = Settings()
