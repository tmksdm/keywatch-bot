from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update

import aiosqlite

from bot.config import ADMIN_ID
from bot.db import DB_PATH


class AuthMiddleware(BaseMiddleware):
    """Пропускает только зарегистрированных пользователей.
    Админ автоматически создаётся при первом обращении.
    Неизвестные — игнор + уведомление админу.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Извлекаем tg_id из любого типа update
        event_user = data.get("event_from_user")
        if event_user is None:
            # Не от пользователя (канал, чат) — пропускаем
            return

        tg_id = event_user.id
        username = event_user.username or ""

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # --- Админ: автосоздание при первом обращении ---
            if tg_id == ADMIN_ID:
                row = await db.execute(
                    "SELECT id, username FROM users WHERE tg_id = ?", (tg_id,)
                )
                existing = await row.fetchone()
                if not existing:
                    await db.execute(
                        "INSERT INTO users (tg_id, username, is_admin) VALUES (?, ?, 1)",
                        (tg_id, username),
                    )
                    await db.commit()
                elif username and existing[1] != username:
                    await db.execute(
                        "UPDATE users SET username = ? WHERE tg_id = ?",
                        (username, tg_id),
                    )
                    await db.commit()
                data["is_admin"] = True
                return await handler(event, data)

            # --- Зарегистрированный пользователь ---
            row = await db.execute(
                "SELECT id, username FROM users WHERE tg_id = ?", (tg_id,)
            )
            existing = await row.fetchone()
            if existing:
                # Обновляем username если изменился
                if username and existing[1] != username:
                    await db.execute(
                        "UPDATE users SET username = ? WHERE tg_id = ?",
                        (username, tg_id),
                    )
                    await db.commit()
                data["is_admin"] = False
                return await handler(event, data)

        # --- Пропускаем userbot (он форвардит посты боту) ---
        from bot.userbot.monitor import userbot_id
        if userbot_id and tg_id == userbot_id:
            data["is_admin"] = False
            return await handler(event, data)

        # --- Неизвестный: тишина + уведомление админу ---
        bot: Bot = data["bot"]
        mention = f"@{username}" if username else "без username"
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ Неизвестный пользователь {mention} "
                f"(ID: {tg_id}) попытался использовать бота",
            )
        except Exception:
            pass

        return  # Игнорируем неизвестного
    