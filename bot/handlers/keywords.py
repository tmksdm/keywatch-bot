from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import aiosqlite

from bot.db import DB_PATH
from bot.handlers.channels import get_user_internal_id

router = Router()


# ── FSM ──

class AddKeyword(StatesGroup):
    waiting_for_input = State()


class AddKeywordForUser(StatesGroup):
    waiting_for_input = State()


# ══════════════════════════════════════════════
#  СВОИ КЛЮЧЕВЫЕ СЛОВА
# ══════════════════════════════════════════════

@router.callback_query(F.data == "keywords")
async def keywords_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список", callback_data="kw_list")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="kw_add")],
        [InlineKeyboardButton(text="➖ Удалить", callback_data="kw_del")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])
    await callback.message.edit_text("🔑 Мои ключевые слова:", reply_markup=kb)
    await callback.answer()


# ── Список своих ──

@router.callback_query(F.data == "kw_list")
async def kw_list(callback: CallbackQuery):
    user_id = await get_user_internal_id(callback.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT keyword FROM keywords WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        text = "Список ключевых слов пуст."
    else:
        lines = [f"{i}. {r[0]}" for i, r in enumerate(rows, 1)]
        text = "🔑 Мои ключевые слова:\n\n" + "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="keywords")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ── Добавить (свои) ──

@router.callback_query(F.data == "kw_add")
async def kw_add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddKeyword.waiting_for_input)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="keywords")],
    ])
    await callback.message.edit_text(
        "Отправь ключевые слова (по одному на строку):",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(AddKeyword.waiting_for_input)
async def kw_add_process(message: Message, state: FSMContext):
    user_id = await get_user_internal_id(message.from_user.id)
    lines = message.text.strip().splitlines()

    added = []
    skipped_dup = []
    skipped_empty = 0

    async with aiosqlite.connect(DB_PATH) as db:
        for raw in lines:
            kw = raw.strip().lower()
            if not kw:
                skipped_empty += 1
                continue
            try:
                await db.execute(
                    "INSERT INTO keywords (user_id, keyword) VALUES (?, ?)",
                    (user_id, kw),
                )
                added.append(kw)
            except aiosqlite.IntegrityError:
                skipped_dup.append(kw)
        await db.commit()

    parts = []
    if added:
        parts.append(f"✅ Добавлено ({len(added)}):\n" + "\n".join(added))
    if skipped_dup:
        parts.append(f"⚠️ Уже есть ({len(skipped_dup)}):\n" + "\n".join(skipped_dup))
    if not parts:
        parts.append("Ничего не добавлено.")

    await state.clear()
    await message.answer(
        "\n\n".join(parts),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="keywords")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")],
        ]),
    )


# ── Удалить (свои) ──

@router.callback_query(F.data == "kw_del")
async def kw_del_list(callback: CallbackQuery):
    user_id = await get_user_internal_id(callback.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, keyword FROM keywords WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await callback.answer("Нет слов для удаления", show_alert=True)
        return

    buttons = []
    for r in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {r[1]}",
                callback_data=f"kw_rm_{r[0]}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="keywords")])

    await callback.message.edit_text(
        "Выбери слово для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kw_rm_"))
async def kw_del_execute(callback: CallbackQuery):
    kw_id = int(callback.data.split("_")[2])
    user_id = await get_user_internal_id(callback.from_user.id)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT keyword FROM keywords WHERE id = ? AND user_id = ?", (kw_id, user_id)
        )
        row = await cur.fetchone()
        if not row:
            await callback.answer("Слово не найдено", show_alert=True)
            return

        keyword_name = row[0]
        await db.execute("DELETE FROM keywords WHERE id = ?", (kw_id,))
        await db.commit()

    await callback.answer(f"Удалено: {keyword_name}", show_alert=True)
    await kw_del_list(callback)


# ══════════════════════════════════════════════
#  КЛЮЧЕВЫЕ СЛОВА ДРУГОГО ПОЛЬЗОВАТЕЛЯ (админ)
# ══════════════════════════════════════════════

# ВАЖНО: специфичные обработчики (ukw_list_, ukw_add_, ukw_del_, ukwrm_)
# стоят ВЫШЕ общего ukw_{tg_id}

# ── Список ──

@router.callback_query(F.data.startswith("ukw_list_"))
async def ukw_list(callback: CallbackQuery, is_admin: bool):
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
            "SELECT keyword FROM keywords WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        text = f"У пользователя {tg_id} нет ключевых слов."
    else:
        lines = [f"{i}. {r[0]}" for i, r in enumerate(rows, 1)]
        text = f"🔑 Слова пользователя {tg_id}:\n\n" + "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"ukw_{tg_id}")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ── Добавить ──

@router.callback_query(F.data.startswith("ukw_add_"))
async def ukw_add_start(callback: CallbackQuery, is_admin: bool, state: FSMContext):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[2])
    await state.set_state(AddKeywordForUser.waiting_for_input)
    await state.update_data(target_tg_id=tg_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"ukw_{tg_id}")],
    ])
    await callback.message.edit_text(
        f"Отправь ключевые слова для пользователя {tg_id} (по одному на строку):",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(AddKeywordForUser.waiting_for_input)
async def ukw_add_process(message: Message, state: FSMContext, is_admin: bool):
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
    skipped_dup = []

    async with aiosqlite.connect(DB_PATH) as db:
        for raw in lines:
            kw = raw.strip().lower()
            if not kw:
                continue
            try:
                await db.execute(
                    "INSERT INTO keywords (user_id, keyword) VALUES (?, ?)",
                    (user_id, kw),
                )
                added.append(kw)
            except aiosqlite.IntegrityError:
                skipped_dup.append(kw)
        await db.commit()

    parts = []
    if added:
        parts.append(f"✅ Добавлено ({len(added)}):\n" + "\n".join(added))
    if skipped_dup:
        parts.append(f"⚠️ Уже есть ({len(skipped_dup)}):\n" + "\n".join(skipped_dup))
    if not parts:
        parts.append("Ничего не добавлено.")

    await state.clear()
    await message.answer(
        "\n\n".join(parts),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Слова пользователя", callback_data=f"ukw_{tg_id}")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")],
        ]),
    )


# ── Удалить ──

@router.callback_query(F.data.startswith("ukw_del_"))
async def ukw_del_list(callback: CallbackQuery, is_admin: bool):
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
            "SELECT id, keyword FROM keywords WHERE user_id = ? ORDER BY id", (user_id,)
        )
        rows = await cur.fetchall()

    if not rows:
        await callback.answer("Нет слов для удаления", show_alert=True)
        return

    buttons = []
    for r in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {r[1]}",
                callback_data=f"ukwrm_{r[0]}_{tg_id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"ukw_{tg_id}")])

    await callback.message.edit_text(
        f"Удалить слово у пользователя {tg_id}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ukwrm_"))
async def ukw_del_execute(callback: CallbackQuery, is_admin: bool):
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    parts = callback.data.split("_")
    kw_id = int(parts[1])
    tg_id = int(parts[2])

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT keyword FROM keywords WHERE id = ?", (kw_id,))
        row = await cur.fetchone()
        if not row:
            await callback.answer("Слово не найдено", show_alert=True)
            return
        keyword_name = row[0]
        await db.execute("DELETE FROM keywords WHERE id = ?", (kw_id,))
        await db.commit()

    await callback.answer(f"Удалено: {keyword_name}", show_alert=True)
    await ukw_del_list(callback)


# ── Подменю слов пользователя (общий обработчик — ПОСЛЕДНИЙ из ukw_) ──

@router.callback_query(F.data.startswith("ukw_"))
async def user_keywords_menu(callback: CallbackQuery, is_admin: bool):
    """Подменю ключевых слов конкретного пользователя (для админа)."""
    if not is_admin:
        await callback.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список", callback_data=f"ukw_list_{tg_id}")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data=f"ukw_add_{tg_id}")],
        [InlineKeyboardButton(text="➖ Удалить", callback_data=f"ukw_del_{tg_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"user_manage_{tg_id}")],
    ])
    await callback.message.edit_text(
        f"🔑 Ключевые слова пользователя {tg_id}:", reply_markup=kb
    )
    await callback.answer()
