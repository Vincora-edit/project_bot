"""
Инициализация Telegram бота.

Bot и Dispatcher для aiogram.
"""

from aiogram import Bot, Dispatcher

from src.config import settings


# Telegram бот
bot = Bot(token=settings.telegram_token)

# Диспетчер
dp = Dispatcher()
