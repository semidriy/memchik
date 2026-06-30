import asyncpg
from pathlib import Path
from bot.config import settings

_pool: asyncpg.Pool | None = None
_SCHEMA = Path(__file__).parent.parent.parent / "migrations" / "schema.sql"


async def create_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    if _SCHEMA.exists():
        async with _pool.acquire() as conn:
            await conn.execute(_SCHEMA.read_text(encoding="utf-8"))
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool not initialized")
    return _pool
