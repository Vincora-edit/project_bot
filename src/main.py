"""
Точка входа приложения.

Запуск:
    python -m src.main

Или:
    python src/main.py
"""

import asyncio

from aiohttp import web

from src.config import settings
from src.core import bot, dp
from src.handlers import commands_router, messages_router
from src.handlers.messages import set_scheduler
from src.services import SchedulerService
from src.webhooks import create_webhook_app
from src.utils.logging import get_logger


logger = get_logger(__name__)


async def start_webhook_server():
    """Запуск webhook-сервера."""
    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.webhook_port)
    await site.start()
    logger.info(f"Webhook-сервер запущен на порту {settings.webhook_port}")
    logger.info("  /bitrix/stage — смена стадии")
    logger.info("  /bitrix/document — счета и акты")
    logger.info("  /bitrix/nps — NPS-опросы")


async def main():
    """Главная функция запуска бота."""
    logger.info("Бот-проджект запускается...")
    logger.info(f"Отслеживаю {len(settings.project_ids)} проджектов")
    logger.info("Напоминания: 15 / 30 / 60 минут от сообщения клиента")

    # Регистрируем роутеры
    dp.include_router(commands_router)
    dp.include_router(messages_router)

    # Запуск webhook-сервера
    await start_webhook_server()

    # Запуск планировщика
    scheduler_service = SchedulerService()
    scheduler_service.start()

    # Передаём планировщик в обработчик сообщений
    set_scheduler(scheduler_service.get_scheduler())

    logger.info("Ctrl+C для остановки")

    # Запуск polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
