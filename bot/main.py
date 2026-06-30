import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

from bot.config import settings
from bot.database import create_pool, close_pool
from bot.middlewares.user import UserMiddleware
from bot.middlewares.op_recheck import OpRecheckMiddleware
from bot.handlers import start, inline_handler, meme, user_templates, user_nav
from bot.handlers.admin import panel, broadcast, links, op, stats, templates, messages, moderation, placements, displays, admins, languages
from bot.services.inline_processor import prewarm_templates
from bot.services.i18n import seed_defaults as seed_i18n_defaults

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(UserMiddleware())
    dp.callback_query.outer_middleware(OpRecheckMiddleware())

    @dp.error()
    async def error_handler(event: ErrorEvent):
        if isinstance(event.exception, TelegramBadRequest):
            msg = str(event.exception)
            if "message is not modified" in msg or "query is too old" in msg:
                return True
        logger.error("Unhandled error: %s", event.exception, exc_info=event.exception)
        return False

    dp.include_router(panel.router)
    dp.include_router(broadcast.router)
    dp.include_router(links.router)
    dp.include_router(op.router)
    dp.include_router(stats.router)
    dp.include_router(templates.router)
    dp.include_router(messages.router)
    dp.include_router(moderation.router)
    dp.include_router(placements.router)
    dp.include_router(displays.router)
    dp.include_router(admins.router)
    dp.include_router(languages.router)
    dp.include_router(start.router)
    dp.include_router(user_nav.router)
    dp.include_router(user_templates.router)
    dp.include_router(meme.router)
    dp.include_router(inline_handler.router)

    await create_pool()
    try:
        await seed_i18n_defaults()
    except Exception as e:
        logger.warning("seed_i18n_defaults failed: %s", e)
    logger.info("Bot started, pre-warming template cache...")
    asyncio.create_task(prewarm_templates(bot))

    try:
        await dp.start_polling(
            bot,
            allowed_updates=list(set(dp.resolve_used_update_types() + ["pre_checkout_query", "chosen_inline_result", "chat_join_request"])),
        )
    finally:
        await close_pool()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
