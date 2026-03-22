import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import BOT_TOKEN
from bot.db import init_db
from bot.handlers.start import router as start_router
from bot.middlewares.auth import AuthMiddleware



async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Инициализация БД
    await init_db()

    # Создание бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.update.middleware(AuthMiddleware())    

    # Подключение хэндлеров
    dp.include_router(start_router)

    logging.info("Бот запускается...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
