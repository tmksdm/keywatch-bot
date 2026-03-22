from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.keyboards.menus import main_menu

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, is_admin: bool = False):
    await message.answer("Главное меню:", reply_markup=main_menu(is_admin))
    