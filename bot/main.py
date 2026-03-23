import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import BOT_TOKEN
from bot.db import init_db
from bot.handlers.start import router as start_router
from bot.handlers.users import router as users_router
from bot.handlers.channels import router as channels_router
from bot.handlers.keywords import router as keywords_router
from bot.handlers.pause import router as pause_router
from bot.middlewares.auth import AuthMiddleware
from bot.userbot.monitor import start_userbot, client as telethon_client


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Инициализация БД
    await init_db()

    # Запуск userbot (Telethon)
    await start_userbot()

    # Создание бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.update.middleware(AuthMiddleware())

    # Подключение хэндлеров
    dp.include_router(start_router)
    dp.include_router(users_router)
    dp.include_router(channels_router)
    dp.include_router(keywords_router)
    dp.include_router(pause_router)

    logging.info("Бот запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        await telethon_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
    