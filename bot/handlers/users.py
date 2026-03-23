from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import aiosqlite

import logging
log = logging.getLogger(__name__)

from bot.db import DB_PATH
from bot.keyboards.menus import main_menu

router = Router()


class AddUser(StatesGroup):
    waiting_for_input = State()


class DeleteUser(StatesGroup):
    waiting_for_confirm = State()


# ── Кнопка «👥 Пользователи» из главного меню ──

@router.callback_query(F.data == "users")
async def users_menu(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список", callback_data="users_list")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="users_add")],
        [InlineKeyboardButton(text="➖ Удалить", callback_data="users_delete")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])
    await callback.message.edit_text("👥 Пользователи:", reply_markup=kb)
    await callback.answer()


# ── Назад в главное меню ──

@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, is_admin: bool, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu(is_admin))
    await callback.answer()


# ── Список пользователей ──

@router.callback_query(F.data == "users_list")
async def users_list(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT tg_id, username, is_admin, paused_until FROM users ORDER BY id"
        )
        rows = await cursor.fetchall()

    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="users")],
        ])
        await callback.message.edit_text("Пользователей нет.", reply_markup=kb)
        await callback.answer()
        return

    buttons = []
    for r in rows:
        name = f"@{r['username']}" if r['username'] else f"ID:{r['tg_id']}"
        role = " 👑" if r['is_admin'] else ""
        pause = " ⏸" if r['paused_until'] else ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{name} ({r['tg_id']}){role}{pause}",
                callback_data=f"user_manage_{r['tg_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="users")])

    await callback.message.edit_text(
        "👥 Пользователи (нажми для управления):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ── Добавить пользователя ──

@router.callback_query(F.data == "users_add")
async def users_add_start(callback: CallbackQuery, is_admin: bool, state: FSMContext):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AddUser.waiting_for_input)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="users")],
    ])
    await callback.message.edit_text(
        "Отправь Telegram ID или @username пользователя:",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(AddUser.waiting_for_input)
async def users_add_process(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()

    tg_id = None
    username = None

    if text.isdigit():
        tg_id = int(text)
        # Пробуем получить username через Telethon
        try:
            from bot.userbot.monitor import client as telethon_client
            if telethon_client.is_connected():
                entity = await telethon_client.get_entity(tg_id)
                username = getattr(entity, "username", None)
        except Exception:
            pass  # Не страшно, добавим без username

    elif text.startswith("@") and len(text) > 1:
        username = text[1:]
        # Резолвим username → tg_id через Telethon
        try:
            from bot.userbot.monitor import client as telethon_client
            if telethon_client.is_connected():
                entity = await telethon_client.get_entity(text)
                tg_id = entity.id
        except Exception:
            pass

        if tg_id is None:
            # Фоллбэк — пробуем через бота
            try:
                chat = await bot.get_chat(f"@{username}")
                tg_id = chat.id
            except Exception:
                await message.answer(
                    f"❌ Не удалось найти пользователя {text}.\n"
                    "Убедись, что username правильный, или отправь числовой ID.",
                )
                return
    else:
        await message.answer("❌ Отправь числовой ID или @username.")
        return

    # Сохраняем в БД
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        if await cursor.fetchone():
            await message.answer(f"⚠️ Пользователь {tg_id} уже зарегистрирован.")
            await state.clear()
            return

        await db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (tg_id, username),
        )
        await db.commit()

    log.info("Админ добавил пользователя: tg_id=%d, username=%s", tg_id, username)        

    await state.clear()
    name_display = f"@{username} ({tg_id})" if username else str(tg_id)
    await message.answer(
        f"✅ Пользователь {name_display} добавлен.",
        reply_markup=main_menu(is_admin=True),
    )


# ── Удалить пользователя ──

@router.callback_query(F.data == "users_delete")
async def users_delete_start(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT tg_id, username FROM users WHERE is_admin = 0 ORDER BY id"
        )
        rows = await cursor.fetchall()

    if not rows:
        await callback.answer("Нет пользователей для удаления", show_alert=True)
        return

    buttons = []
    for r in rows:
        name = f"@{r['username']}" if r['username'] else str(r['tg_id'])
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {name} ({r['tg_id']})",
                callback_data=f"users_del_{r['tg_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="users")])

    await callback.message.edit_text(
        "Выбери пользователя для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("users_del_"))
async def users_delete_confirm(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"users_confirm_{tg_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="users"),
        ],
    ])
    await callback.message.edit_text(
        f"Удалить пользователя {tg_id} и все его каналы/слова?",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("users_confirm_"))
async def users_delete_execute(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем внутренний id
        cursor = await db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = await cursor.fetchone()
        if not row:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        user_id = row[0]

        # Удаляем каналы, ключевые слова, потом пользователя
        await db.execute("DELETE FROM channels WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM keywords WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()

    log.info("Админ удалил пользователя: tg_id=%d", tg_id)        

    await callback.message.edit_text(
        f"✅ Пользователь {tg_id} удалён.",
        reply_markup=main_menu(is_admin=True),
    )
    await callback.answer()
