"""
Настройка логирования.

Структурированное логирование с поддержкой файлов и консоли.
"""

import logging
import sys
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    Создаёт и настраивает логгер.

    Args:
        name: Имя логгера (обычно __name__)

    Returns:
        logging.Logger: Настроенный логгер
    """
    logger = logging.getLogger(name)

    # Если уже настроен — возвращаем как есть
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # Формат сообщений
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Консольный хендлер
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Файловый хендлер (опционально)
    log_dir = Path("/var/log/project-bot")
    if log_dir.exists() and log_dir.is_dir():
        file_handler = logging.FileHandler(log_dir / "bot.log")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Глобальный логгер для быстрого доступа
log = get_logger("project_bot")
