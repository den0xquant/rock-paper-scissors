import asyncio
from aiogram import Bot, Dispatcher

from server.bot.handlers import router
from server.config import settings


async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    allowed = dp.resolve_used_update_types()
    await dp.start_polling(bot, allowed_updates=allowed)


if __name__ == "__main__":
    asyncio.run(main())
