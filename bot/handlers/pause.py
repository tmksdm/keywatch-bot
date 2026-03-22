from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import aiosqlite

from bot.db import DB_PATH
from bot.handlers.channels import get_user_internal_id
from bot.keyboards.menus import main_menu

router = Router()

# Варианты сроков паузы: текст → дней
PAUSE_OPTIONS = [
    ("1 день", 1),
    ("3 дня", 3),
    ("1 неделя", 7),
    ("2 недели", 14),
    ("1 месяц", 30),
]


# ── Утилиты ──

async def _get_pause_info(tg_id: int) -> str | None:
    """Возвращает paused_until (строка ISO) или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT paused_until FROM users WHERE tg_id = ?", (tg_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None


def _format_pause(paused_until_str: str | None) -> str:
    """Человекочитаемый статус паузы."""
    if not paused_until_str:
        return "▶️ Мониторинг активен"
    dt = datetime.fromisoformat(paused_until_str)
    now = datetime.now(timezone.utc)
    if dt <= now:
        return "▶️ Мониторинг активен"
    return f"⏸ Пауза до {dt.strftime('%d.%m.%Y %H:%M')} UTC"


# ══════════════════════════════════════════════
#  СВОЯ ПАУЗА
# ══════════════════════════════════════════════

@router.callback_query(F.data == "pause")
async def pause_menu(callback: CallbackQuery):
    paused_until_str = await _get_pause_info(callback.from_user.id)
    status = _format_pause(paused_until_str)

    buttons = []

    # Если пауза активна — кнопка «Снять паузу»
    if paused_until_str:
        dt = datetime.fromisoformat(paused_until_str)
        if dt > datetime.now(timezone.utc):
            buttons.append([
                InlineKeyboardButton(text="▶️ Снять паузу", callback_data="pause_off")
            ])

    # Кнопки выбора срока
    for label, days in PAUSE_OPTIONS:
        buttons.append([
            InlineKeyboardButton(
                text=f"⏸ {label}",
                callback_data=f"pause_set_{days}",
            )
        ])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])

    await callback.message.edit_text(
        f"⏸ Пауза мониторинга\n\n{status}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ── Установить паузу (своя) ──

@router.callback_query(F.data.startswith("pause_set_"))
async def pause_set(callback: CallbackQuery):
    days = int(callback.data.split("_")[2])
    until = datetime.now(timezone.utc) + timedelta(days=days)
    until_str = until.isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET paused_until = ? WHERE tg_id = ?",
            (until_str, callback.from_user.id),
        )
        await db.commit()

    await callback.answer(
        f"Пауза установлена до {until.strftime('%d.%m.%Y %H:%M')} UTC",
        show_alert=True,
    )
    # Обновляем меню
    await pause_menu(callback)


# ── Снять паузу (своя) ──

@router.callback_query(F.data == "pause_off")
async def pause_off(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET paused_until = NULL WHERE tg_id = ?",
            (callback.from_user.id,),
        )
        await db.commit()

    await callback.answer("Мониторинг возобновлён!", show_alert=True)
    await pause_menu(callback)


# ══════════════════════════════════════════════
#  ПАУЗА ДРУГОГО ПОЛЬЗОВАТЕЛЯ (только админ)
# ══════════════════════════════════════════════

# Специфичные обработчики ВЫШЕ общего upause_{tg_id}

@router.callback_query(F.data.startswith("upause_set_"))
async def upause_set(callback: CallbackQuery, is_admin: bool):
    """upause_set_{days}_{tg_id}"""
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    days = int(parts[2])
    tg_id = int(parts[3])

    until = datetime.now(timezone.utc) + timedelta(days=days)
    until_str = until.isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET paused_until = ? WHERE tg_id = ?",
            (until_str, tg_id),
        )
        await db.commit()

    await callback.answer(
        f"Пауза установлена до {until.strftime('%d.%m.%Y %H:%M')} UTC",
        show_alert=True,
    )
    # Обновляем меню — подменяем callback.data для повторного вызова
    callback.data = f"upause_{tg_id}"
    await upause_menu(callback, is_admin)


@router.callback_query(F.data.startswith("upause_off_"))
async def upause_off(callback: CallbackQuery, is_admin: bool):
    """upause_off_{tg_id}"""
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET paused_until = NULL WHERE tg_id = ?",
            (tg_id,),
        )
        await db.commit()

    await callback.answer("Мониторинг возобновлён!", show_alert=True)
    callback.data = f"upause_{tg_id}"
    await upause_menu(callback, is_admin)


# ── Подменю паузы пользователя (общий обработчик — ПОСЛЕДНИЙ из upause_) ──

@router.callback_query(F.data.startswith("upause_"))
async def upause_menu(callback: CallbackQuery, is_admin: bool):
    """Меню паузы для конкретного пользователя (админ)."""
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[1])
    paused_until_str = await _get_pause_info(tg_id)
    status = _format_pause(paused_until_str)

    buttons = []

    if paused_until_str:
        dt = datetime.fromisoformat(paused_until_str)
        if dt > datetime.now(timezone.utc):
            buttons.append([
                InlineKeyboardButton(
                    text="▶️ Снять паузу",
                    callback_data=f"upause_off_{tg_id}",
                )
            ])

    for label, days in PAUSE_OPTIONS:
        buttons.append([
            InlineKeyboardButton(
                text=f"⏸ {label}",
                callback_data=f"upause_set_{days}_{tg_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"user_manage_{tg_id}")
    ])

    await callback.message.edit_text(
        f"⏸ Пауза пользователя {tg_id}\n\n{status}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()
