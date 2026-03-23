from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.config import ADMIN_ID
from bot.keyboards.menus import main_menu

import logging

log = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, is_admin: bool = False):
    await message.answer("Главное меню:", reply_markup=main_menu(is_admin))


# Буфер для галерей на стороне бота: channel → {message_ids, timer}
_bot_gallery_buffer: dict[str, dict] = {}


@router.message(F.forward_origin)
async def handle_forwarded_to_bot(message: Message):
    """Ловим форварды от userbot → рассылаем пользователям."""
    from bot.userbot.monitor import _pending_recipients, userbot_id
    import asyncio

    if not userbot_id or message.from_user.id != userbot_id:
        return

    origin = message.forward_origin
    channel = None

    if hasattr(origin, "chat") and origin.chat:
        username = origin.chat.username
        if username:
            channel = f"@{username.lower()}"

    if not channel:
        return

    # Буферизуем сообщения галереи
    if channel not in _bot_gallery_buffer:
        _bot_gallery_buffer[channel] = {"messages": []}

    _bot_gallery_buffer[channel]["messages"].append(message)

    # Отменяем предыдущий таймер если был
    old_task = _bot_gallery_buffer[channel].get("task")
    if old_task:
        old_task.cancel()

    # Ставим таймер на 2 сек — потом рассылаем
    async def flush():
        await asyncio.sleep(2)
        buf = _bot_gallery_buffer.pop(channel, None)
        if not buf:
            return

        info = _pending_recipients.pop(channel, None)
        if not info:
            log.warning("Нет recipients для канала %s, пропускаем", channel)
            return

        recipients = info["recipients"]
        msgs = buf["messages"]

        msg_ids = [m.message_id for m in msgs]
        from_chat = message.chat.id  # чат userbot↔бот

        for r in recipients:
            try:
                await message.bot.forward_messages(
                    chat_id=r["tg_id"],
                    from_chat_id=from_chat,
                    message_ids=msg_ids,
                )

                words_str = ", ".join(r["matched"])
                await message.bot.send_message(
                    r["tg_id"],
                    f"🔑 Сработали: {words_str}",
                )
            except Exception as e:
                log.error(
                    "Ошибка рассылки для tg_id=%d, канал=%s: %s",
                    r["tg_id"], channel, e,
                )

    _bot_gallery_buffer[channel]["task"] = asyncio.create_task(flush())
