import asyncio
import logging
import signal
import sys

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

log = logging.getLogger(__name__)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Telethon слишком болтливый на INFO
    logging.getLogger("telethon").setLevel(logging.WARNING)

    log.info("Инициализация БД...")
    await init_db()

    log.info("Запуск userbot...")
    await start_userbot()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.update.middleware(AuthMiddleware())

    dp.include_router(start_router)
    dp.include_router(users_router)
    dp.include_router(channels_router)
    dp.include_router(keywords_router)
    dp.include_router(pause_router)

    # Graceful shutdown по SIGTERM (systemd) и SIGINT (Ctrl+C)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("Получен сигнал завершения, останавливаемся...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    log.info("Бот запускается...")
    try:
        polling_task = asyncio.create_task(dp.start_polling(bot))

        if sys.platform != "win32":
            await stop_event.wait()
            log.info("Остановка polling...")
            await dp.stop_polling()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        else:
            await polling_task
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Отключаем Telethon...")
        try:
            await telethon_client.disconnect()
        except Exception:
            pass
        log.info("Закрываем сессию бота...")
        await bot.session.close()
        log.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
