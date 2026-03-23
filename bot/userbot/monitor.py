"""
Telethon userbot — мониторинг каналов по ключевым словам.
"""

import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events, errors
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
import aiosqlite

from bot.config import API_ID, API_HASH, BOT_TOKEN
from bot.db import DB_PATH

log = logging.getLogger(__name__)

SESSION_PATH = "userbot"
client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

# Словарь ожидающих форвардов: message_id → list[dict]
# После форварда боту — бот подхватит и разошлёт пользователям
# Очередь рассылки: channel → list[{tg_id, matched}]
_pending_recipients: dict[str, list[dict]] = {}
# Буфер для галерей: grouped_id → {messages, channel, recipients, task}
_gallery_buffer: dict[int, dict] = {}


# ══════════════════════════════════════════════
#  Вспомогательные функции
# ══════════════════════════════════════════════

async def get_all_monitored_channels() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT DISTINCT channel FROM channels")
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_users_for_channel(channel: str) -> list[dict]:
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT u.tg_id, u.username, u.paused_until
            FROM users u
            JOIN channels c ON c.user_id = u.id
            WHERE c.channel = ?
            """,
            (channel,),
        )
        users_rows = await cur.fetchall()

        result = []
        for u in users_rows:
            if u["paused_until"] and u["paused_until"] > now_iso:
                continue

            kw_cur = await db.execute(
                """
                SELECT keyword FROM keywords
                WHERE user_id = (SELECT id FROM users WHERE tg_id = ?)
                """,
                (u["tg_id"],),
            )
            kw_rows = await kw_cur.fetchall()
            keywords = [r[0] for r in kw_rows]
            if keywords:
                result.append({
                    "tg_id": u["tg_id"],
                    "username": u["username"],
                    "keywords": keywords,
                })
    return result


def find_matches(text: str, keywords: list[str]) -> list[str]:
    text_lower = text.lower()
    return [kw for kw in keywords if kw in text_lower]


# ══════════════════════════════════════════════
#  Подписка / отписка
# ══════════════════════════════════════════════

async def ensure_joined(channel: str) -> bool:
    try:
        entity = await client.get_entity(channel)
        try:
            await client.get_permissions(entity, "me")
            return True
        except (errors.UserNotParticipantError, errors.ChannelPrivateError):
            pass
        except Exception:
            pass

        await client(JoinChannelRequest(entity))
        log.info("Userbot подписался на %s", channel)
        return True

    except errors.ChannelPrivateError:
        log.warning("Канал %s приватный", channel)
        return False
    except errors.FloodWaitError as e:
        log.warning("FloodWait %d сек при подписке на %s", e.seconds, channel)
        return False
    except Exception as e:
        log.error("Не удалось подписаться на %s: %s", channel, e)
        return False


async def leave_if_unused(channel: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM channels WHERE channel = ?", (channel,)
        )
        row = await cur.fetchone()
        if row[0] > 0:
            return

    try:
        entity = await client.get_entity(channel)
        await client(LeaveChannelRequest(entity))
        log.info("Userbot отписался от %s", channel)
    except Exception as e:
        log.warning("Не удалось отписаться от %s: %s", channel, e)


# ══════════════════════════════════════════════
#  Обработчик новых постов
# ══════════════════════════════════════════════

userbot_id: int | None = None
_bot_entity = None


async def _get_bot_entity():
    """Получаем entity бота для пересылки ему сообщений."""
    global _bot_entity
    if _bot_entity is None:
        # Извлекаем username бота из токена
        from aiogram import Bot
        bot = Bot(token=BOT_TOKEN)
        try:
            me = await bot.get_me()
            _bot_entity = await client.get_entity(f"@{me.username}")
        finally:
            await bot.session.close()
    return _bot_entity


@client.on(events.NewMessage())
async def on_new_message(event):
    if not event.is_channel:
        return

    text = event.raw_text
    if not text and not getattr(event.message, "grouped_id", None):
        return

    chat = await event.get_chat()
    if not chat or not getattr(chat, "username", None):
        return

    channel = f"@{chat.username.lower()}"

    grouped_id = getattr(event.message, "grouped_id", None)

    # ── Галерея ──
    if grouped_id:
        if grouped_id in _gallery_buffer:
            # Добавляем сообщение в существующий буфер
            _gallery_buffer[grouped_id]["messages"].append(event.message)
            return

        # Первое сообщение галереи — нужен текст для проверки ключевых слов
        # Текст обычно в первом сообщении галереи
        gallery_text = event.raw_text or ""

        users = await get_users_for_channel(channel)
        if not users:
            return

        recipients = []
        for u in users:
            if gallery_text:
                matched = find_matches(gallery_text, u["keywords"])
                if matched:
                    recipients.append({"tg_id": u["tg_id"], "matched": matched})

        if not recipients and not gallery_text:
            return

        _gallery_buffer[grouped_id] = {
            "messages": [event.message],
            "channel": channel,
            "recipients": recipients,
            "text": gallery_text,
        }

        # Запускаем отложенную отправку — ждём 3 сек пока придут все части
        asyncio.create_task(_send_gallery_delayed(grouped_id))
        return

    # ── Одиночный пост ──
    if not text:
        return

    users = await get_users_for_channel(channel)
    if not users:
        return

    recipients = []
    for u in users:
        matched = find_matches(text, u["keywords"])
        if matched:
            recipients.append({"tg_id": u["tg_id"], "matched": matched})

    if not recipients:
        return

    _pending_recipients[channel] = {
        "recipients": recipients,
        "timestamp": asyncio.get_event_loop().time(),
    }

    try:
        bot_entity = await _get_bot_entity()
        await client.forward_messages(bot_entity, event.message)
    except Exception as e:
        log.error("Ошибка форварда боту, канал=%s: %s", channel, e)
        _pending_recipients.pop(channel, None)


async def _send_gallery_delayed(grouped_id: int):
    """Ждём 3 сек, потом форвардим всю галерею боту."""
    await asyncio.sleep(3)

    data = _gallery_buffer.pop(grouped_id, None)
    if not data:
        return

    recipients = data["recipients"]
    channel = data["channel"]
    messages = sorted(data["messages"], key=lambda m: m.id)

    # Если текст был не в первом сообщении — проверяем все
    if not recipients:
        full_text = " ".join(m.raw_text for m in messages if m.raw_text)
        if not full_text:
            return

        users = await get_users_for_channel(channel)
        for u in users:
            matched = find_matches(full_text, u["keywords"])
            if matched:
                recipients.append({"tg_id": u["tg_id"], "matched": matched})

    if not recipients:
        return

    _pending_recipients[channel] = {
        "recipients": recipients,
        "timestamp": asyncio.get_event_loop().time(),
    }

    try:
        bot_entity = await _get_bot_entity()
        await client.forward_messages(bot_entity, messages)
    except Exception as e:
        log.error("Ошибка форварда галереи боту, канал=%s: %s", channel, e)
        _pending_recipients.pop(channel, None)


# ══════════════════════════════════════════════
#  Запуск
# ══════════════════════════════════════════════

async def start_userbot():
    log.info("Запуск userbot (Telethon)...")
    await client.start()
    me = await client.get_me()
    global userbot_id
    userbot_id = me.id    
    log.info("Userbot авторизован как %s (ID: %d)", me.username or me.first_name, me.id)

    channels = await get_all_monitored_channels()
    for ch in channels:
        await ensure_joined(ch)
        await asyncio.sleep(1)

    log.info("Userbot мониторит %d каналов.", len(channels))
