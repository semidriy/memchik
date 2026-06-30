import logging
from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from bot.database import get_pool
from bot.database.queries import upsert_user
from bot.services.detector import guess_gender

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: User | None = data.get("event_from_user")
        if tg_user and not tg_user.is_bot:
            try:
                pool = get_pool()
                gender = guess_gender(tg_user.first_name)
                await upsert_user(pool, tg_user, gender)
            except Exception as e:
                logger.error("upsert_user failed for %s: %s", tg_user.id, e)
        return await handler(event, data)
