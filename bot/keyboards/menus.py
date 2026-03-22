from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню. Для админа — дополнительная кнопка."""
    buttons = [
        [InlineKeyboardButton(text="📡 Каналы", callback_data="channels")],
        [InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="keywords")],
        [InlineKeyboardButton(text="⏸ Пауза", callback_data="pause")],
    ]
    if is_admin:
        buttons.append(
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="users")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
