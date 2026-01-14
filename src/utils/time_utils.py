"""
Утилиты для работы с датой и временем.

Функции для проверки рабочего времени, праздников, парсинга timestamp.
"""

import re
from datetime import datetime, timedelta

from src.config import settings, HOLIDAYS


def now_local() -> datetime:
    """
    Возвращает текущее время в часовом поясе компании.

    Returns:
        datetime: Текущее время в timezone компании (Europe/Moscow)
    """
    return datetime.now(settings.timezone)


def parse_timestamp(ts: str) -> datetime:
    """
    Парсит timestamp из Supabase.

    Supabase может вернуть формат с 5 знаками микросекунд
    (например '2025-12-25T17:17:37.72304+00:00'),
    который Python не понимает напрямую.

    Args:
        ts: Строка timestamp из Supabase

    Returns:
        datetime: Распарсенный datetime объект
    """
    # Заменяем Z на +00:00
    ts = ts.replace("Z", "+00:00")
    # Нормализуем микросекунды до 6 знаков
    ts = re.sub(
        r'\.(\d{1,5})([+-])',
        lambda m: f'.{m.group(1).ljust(6, "0")}{m.group(2)}',
        ts
    )
    return datetime.fromisoformat(ts)


def is_work_time(dt: datetime) -> bool:
    """
    Проверяет, попадает ли указанное время в рабочие часы компании.

    Условия:
    - Только будние дни (Пн–Пт)
    - Время между work_start и work_end

    Args:
        dt: Дата и время для проверки

    Returns:
        bool: True если рабочее время, False если нет
    """
    # Выходные
    if dt.weekday() >= 5:  # 5 = суббота, 6 = воскресенье
        return False

    t = dt.time()
    return settings.work_start <= t < settings.work_end


def next_work_start(dt: datetime) -> datetime:
    """
    Возвращает ближайшее начало рабочего дня.

    Args:
        dt: Исходная дата и время

    Returns:
        datetime: Начало ближайшего рабочего дня
    """
    base = dt

    # Если сегодня рабочий день и время раньше начала работы
    if base.weekday() < 5 and base.time() < settings.work_start:
        return base.replace(
            hour=settings.work_start.hour,
            minute=settings.work_start.minute,
            second=0,
            microsecond=0
        )

    # Иначе ищем следующий будний день
    while True:
        base = (base + timedelta(days=1)).replace(
            hour=settings.work_start.hour,
            minute=settings.work_start.minute,
            second=0,
            microsecond=0
        )
        if base.weekday() < 5:
            return base


def is_holiday(dt: datetime) -> bool:
    """
    Проверяет, является ли день праздником.

    Args:
        dt: Дата для проверки

    Returns:
        bool: True если праздник, False если нет
    """
    return (dt.month, dt.day) in HOLIDAYS


def get_holiday_name(dt: datetime) -> str | None:
    """
    Возвращает название праздника для указанной даты.

    Args:
        dt: Дата для проверки

    Returns:
        str | None: Название праздника или None
    """
    return HOLIDAYS.get((dt.month, dt.day))
