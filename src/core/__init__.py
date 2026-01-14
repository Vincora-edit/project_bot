"""Ядро приложения — бот, база данных, планировщик."""

from .database import db
from .bot import bot, dp

__all__ = ["db", "bot", "dp"]
