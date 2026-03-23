from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import aiosqlite
import re

from bot.db import DB_PATH
from bot.keyboards.menus import main_menu
from bot.userbot.monitor import ensure_joined, leave_if_unused, client as telethon_client

router = Router()


# ── FSM ──

class AddChannel(StatesGroup):
    waiting_for_input = State()


class AddChannelForUser(StatesGroup):
    waiting_for_input = State()


# ── Утилиты ──

def normalize_channel(raw: str) -> str | None:
    """Приводит ввод к формату @username. Возвращает None если формат невалиден."""
    raw = raw.strip()
    # https://t.me/username или https://t.me/+invite
    m = re.match(r"https?://t\.me/(\+?\w+)", raw, re.IGNORECASE)
    if m:
        name = m.group(1)
        if name.startswith("+"):
            return None          # приватные каналы не поддерживаем
        return f"@{name.lower()}"
    # @username
    if raw.startswith("@") and len(raw) > 1 and raw[1:].replace("_", "").isalnum():
        return f"@{raw[1:].lower()}"
    # просто username
    if raw.replace("_", "").isalnum() and len(raw) >= 3:
        return f"@{raw.lower()}"
    return None


async def get_user_internal_id(tg_id: int) -> int | None:
    """Возвращает внутренний id из таблицы users по tg_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row else None


# ══════════════════════════════════════════════
#  СВОИ КАНАЛЫ (для любого зарегистрированного)
# ══════════════════════════════════════════════

@router.callback_query(F.data == "channels")
async def channels_menu(callback: CallbackQuery, is_admin: bool):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список", callback_data="ch_list")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="ch_add")],
        [InlineKeyboardButton(text="➖ Удалить", callback_data="ch_del")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])
    await callback.message.edit_text("📡 Мои каналы:", reply_markup=kb)
    await callback.answer()


# ── Список своих каналов ──

@router.callback_query(F.data == "ch_list")
async def ch_list(callback: CallbackQuery):
    user_id = await get_user_internal_id(callback.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT channel FROM channels WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        text = "Список каналов пуст."
    else:
        lines = [f"{i}. {r[0]}" for i, r in enumerate(rows, 1)]
        text = "📡 Мои каналы:\n\n" + "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="channels")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ── Добавить каналы (свои) ──

@router.callback_query(F.data == "ch_add")
async def ch_add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.waiting_for_input)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="channels")],
    ])

    await callback.message.edit_text(
        "Отправь каналы (по одному на строку).\n"
        "Форматы: @channel, https://t.me/channel",
        reply_markup=kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await callback.answer()


@router.message(AddChannel.waiting_for_input)
async def ch_add_process(message: Message, state: FSMContext):
    user_id = await get_user_internal_id(message.from_user.id)
    lines = message.text.strip().splitlines()

    added = []
    skipped_format = []
    skipped_dup = []
    skipped_join = []

    async with aiosqlite.connect(DB_PATH) as db:
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            ch = normalize_channel(raw)
            if ch is None:
                skipped_format.append(raw)
                continue

            # Проверяем дубликат ДО подписки
            cur = await db.execute(
                "SELECT id FROM channels WHERE user_id = ? AND channel = ?",
                (user_id, ch),
            )
            if await cur.fetchone():
                skipped_dup.append(ch)
                continue

            # Подписка userbot
            if telethon_client.is_connected():
                joined = await ensure_joined(ch)
                if not joined:
                    skipped_join.append(ch)
                    continue

            await db.execute(
                "INSERT INTO channels (user_id, channel) VALUES (?, ?)",
                (user_id, ch),
            )
            added.append(ch)
        await db.commit()

    # Сводка
    parts = []
    if added:
        parts.append(f"✅ Добавлено ({len(added)}):\n" + "\n".join(added))
    if skipped_dup:
        parts.append(f"⚠️ Уже есть ({len(skipped_dup)}):\n" + "\n".join(skipped_dup))
    if skipped_join:
        parts.append(f"❌ Не удалось подписаться ({len(skipped_join)}):\n" + "\n".join(skipped_join))
    if skipped_format:
        parts.append(f"❌ Неверный формат ({len(skipped_format)}):\n" + "\n".join(skipped_format))
    if not parts:
        parts.append("Ничего не отправлено.")

    await state.clear()
    await message.answer(
        "\n\n".join(parts),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📡 Каналы", callback_data="channels")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")],
        ]),
    )


# ── Удалить каналы (свои) ──

@router.callback_query(F.data == "ch_del")
async def ch_del_list(callback: CallbackQuery):
    user_id = await get_user_internal_id(callback.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, channel FROM channels WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await callback.answer("Нет каналов для удаления", show_alert=True)
        return

    buttons = []
    for r in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {r[1]}",
                callback_data=f"ch_rm_{r[0]}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="channels")])

    await callback.message.edit_text(
        "Выбери канал для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch_rm_"))
async def ch_del_execute(callback: CallbackQuery):
    ch_id = int(callback.data.split("_")[2])
    user_id = await get_user_internal_id(callback.from_user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем, что канал принадлежит этому пользователю
        cur = await db.execute(
            "SELECT channel FROM channels WHERE id = ? AND user_id = ?", (ch_id, user_id)
        )
        row = await cur.fetchone()
        if not row:
            await callback.answer("Канал не найден", show_alert=True)
            return

        channel_name = row[0]
        await db.execute("DELETE FROM channels WHERE id = ?", (ch_id,))
        await db.commit()

    # Отписка userbot если канал больше никому не нужен
    if telethon_client.is_connected():
        await leave_if_unused(channel_name)

    await callback.answer(f"Удалён: {channel_name}", show_alert=True)
    # Обновляем список
    await ch_del_list(callback)





# ── Список каналов пользователя (админ) ──

@router.callback_query(F.data.startswith("uch_list_"))
async def uch_list(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])
    user_id = await get_user_internal_id(tg_id)
    if user_id is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT channel FROM channels WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        text = f"У пользователя {tg_id} нет каналов."
    else:
        lines = [f"{i}. {r[0]}" for i, r in enumerate(rows, 1)]
        text = f"📡 Каналы пользователя {tg_id}:\n\n" + "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"uch_{tg_id}")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ── Добавить каналы пользователю (админ) ──

@router.callback_query(F.data.startswith("uch_add_"))
async def uch_add_start(callback: CallbackQuery, is_admin: bool, state: FSMContext):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])
    await state.set_state(AddChannelForUser.waiting_for_input)
    await state.update_data(target_tg_id=tg_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"uch_{tg_id}")],
    ])
    await callback.message.edit_text(
        f"Отправь каналы для пользователя {tg_id} (по одному на строку).\n"
        "Форматы: @channel, https://t.me/channel",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(AddChannelForUser.waiting_for_input)
async def uch_add_process(message: Message, state: FSMContext, is_admin: bool):
    if not is_admin:
        await state.clear()
        return

    data = await state.get_data()
    tg_id = data["target_tg_id"]
    user_id = await get_user_internal_id(tg_id)
    if user_id is None:
        await message.answer("Пользователь не найден.")
        await state.clear()
        return

    lines = message.text.strip().splitlines()
    added = []
    skipped_format = []
    skipped_dup = []
    skipped_join = []

    async with aiosqlite.connect(DB_PATH) as db:
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            ch = normalize_channel(raw)
            if ch is None:
                skipped_format.append(raw)
                continue

            # Проверяем дубликат ДО подписки
            cur = await db.execute(
                "SELECT id FROM channels WHERE user_id = ? AND channel = ?",
                (user_id, ch),
            )
            if await cur.fetchone():
                skipped_dup.append(ch)
                continue

            # Подписка userbot
            if telethon_client.is_connected():
                joined = await ensure_joined(ch)
                if not joined:
                    skipped_join.append(ch)
                    continue

            await db.execute(
                "INSERT INTO channels (user_id, channel) VALUES (?, ?)",
                (user_id, ch),
            )
            added.append(ch)
        await db.commit()

    parts = []
    if added:
        parts.append(f"✅ Добавлено ({len(added)}):\n" + "\n".join(added))
    if skipped_dup:
        parts.append(f"⚠️ Уже есть ({len(skipped_dup)}):\n" + "\n".join(skipped_dup))
    if skipped_join:
        parts.append(f"❌ Не удалось подписаться ({len(skipped_join)}):\n" + "\n".join(skipped_join))
    if skipped_format:
        parts.append(f"❌ Неверный формат ({len(skipped_format)}):\n" + "\n".join(skipped_format))
    if not parts:
        parts.append("Ничего не отправлено.")

    await state.clear()
    await message.answer(
        "\n\n".join(parts),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📡 Каналы пользователя", callback_data=f"uch_{tg_id}")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")],
        ]),
    )


# ── Удалить канал у пользователя (админ) ──

@router.callback_query(F.data.startswith("uch_del_"))
async def uch_del_list(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])
    user_id = await get_user_internal_id(tg_id)
    if user_id is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, channel FROM channels WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await callback.answer("Нет каналов для удаления", show_alert=True)
        return

    buttons = []
    for r in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {r[1]}",
                callback_data=f"uchrm_{r[0]}_{tg_id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"uch_{tg_id}")])

    await callback.message.edit_text(
        f"Удалить канал у пользователя {tg_id}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("uchrm_"))
async def uch_del_execute(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    ch_id = int(parts[1])
    tg_id = int(parts[2])

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT channel FROM channels WHERE id = ?", (ch_id,))
        row = await cur.fetchone()
        if not row:
            await callback.answer("Канал не найден", show_alert=True)
            return
        channel_name = row[0]
        await db.execute("DELETE FROM channels WHERE id = ?", (ch_id,))
        await db.commit()

    # Отписка userbot если канал больше никому не нужен
    if telethon_client.is_connected():
        await leave_if_unused(channel_name)

    await callback.answer(f"Удалён: {channel_name}", show_alert=True)
    # Обновляем список
    await uch_del_list(callback)

# ══════════════════════════════════════════════
#  КАНАЛЫ ДРУГОГО ПОЛЬЗОВАТЕЛЯ (только админ)
# ══════════════════════════════════════════════

@router.callback_query(F.data.startswith("uch_"))
async def user_channels_menu(callback: CallbackQuery, is_admin: bool):
    """Подменю каналов конкретного пользователя (для админа)."""
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список", callback_data=f"uch_list_{tg_id}")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data=f"uch_add_{tg_id}")],
        [InlineKeyboardButton(text="➖ Удалить", callback_data=f"uch_del_{tg_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"user_manage_{tg_id}")],
    ])
    await callback.message.edit_text(
        f"📡 Каналы пользователя {tg_id}:", reply_markup=kb
    )
    await callback.answer()


# ══════════════════════════════════════════════
#  Меню управления пользователем (для админа)
# ══════════════════════════════════════════════

@router.callback_query(F.data.startswith("user_manage_"))
async def user_manage_menu(callback: CallbackQuery, is_admin: bool):
    """Меню управления конкретным пользователем — каналы, слова."""
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])

    # Получаем инфо о пользователе
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT tg_id, username FROM users WHERE tg_id = ?", (tg_id,))
        user = await cur.fetchone()

    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    name = f"@{user['username']}" if user['username'] else str(tg_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Каналы", callback_data=f"uch_{tg_id}")],
        [InlineKeyboardButton(text="🔑 Ключевые слова", callback_data=f"ukw_{tg_id}")],
        [InlineKeyboardButton(text="⏸ Пауза", callback_data=f"upause_{tg_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="users_list")],
    ])

    await callback.message.edit_text(
        f"Управление: {name} ({tg_id})", reply_markup=kb
    )
    await callback.answer()
