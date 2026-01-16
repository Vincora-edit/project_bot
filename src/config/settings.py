"""
Настройки и конфигурация приложения.

Все переменные окружения и константы собраны в одном месте.
"""

import os
from datetime import time
from dataclasses import dataclass, field
from typing import List
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


# Загружаем .env
load_dotenv()


@dataclass
class Settings:
    """Основные настройки приложения."""

    # Telegram
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_TOKEN", ""))
    owner_id: int = field(default_factory=lambda: int(os.getenv("OWNER_ID", "1139575259")))
    project_ids: List[int] = field(default_factory=list)

    # OpenAI
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = "gpt-4o-mini"

    # Supabase
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_key: str = field(default_factory=lambda: os.getenv("SUPABASE_KEY", ""))

    # Webhook
    webhook_port: int = field(default_factory=lambda: int(os.getenv("WEBHOOK_PORT", "8081")))
    webhook_secret: str = field(default_factory=lambda: os.getenv("WEBHOOK_SECRET", ""))

    # Dashboard
    dashboard_api_url: str = field(default_factory=lambda: os.getenv("DASHBOARD_API_URL", ""))
    dashboard_bot_secret: str = field(default_factory=lambda: os.getenv("DASHBOARD_BOT_SECRET", ""))

    # Временная зона
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Moscow"))

    # Рабочее время
    work_start: time = time(10, 0)
    work_end: time = time(19, 0)

    # Задержки напоминаний (в секундах)
    escalation_delays: List[int] = field(default_factory=lambda: [15 * 60, 30 * 60, 60 * 60])

    def __post_init__(self):
        """Загрузка PROJECT_IDS из env или дефолтных значений."""
        project_ids_str = os.getenv("PROJECT_IDS", "")
        if project_ids_str:
            self.project_ids = [int(x.strip()) for x in project_ids_str.split(",") if x.strip()]
        else:
            # Дефолтные значения (для совместимости)
            self.project_ids = [
                self.owner_id,
                760732823,   # Кристина
                717802592,   # Наталья
                4739313341,  # Александр
                5269702355,  # Алексей
                904374872,   # Li
            ]

    def validate(self) -> None:
        """Проверка обязательных настроек."""
        errors = []

        if not self.telegram_token:
            errors.append("TELEGRAM_TOKEN not set")
        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY not set")
        if not self.supabase_url:
            errors.append("SUPABASE_URL not set")
        if not self.supabase_key:
            errors.append("SUPABASE_KEY not set")
        if self.owner_id == 0:
            errors.append("OWNER_ID not set")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")


# Праздники (месяц, день): название
HOLIDAYS = {
    (1, 1): "Новый год",
    (1, 2): "Новогодние каникулы",
    (1, 3): "Новогодние каникулы",
    (1, 4): "Новогодние каникулы",
    (1, 5): "Новогодние каникулы",
    (1, 6): "Новогодние каникулы",
    (1, 7): "Рождество",
    (1, 8): "Новогодние каникулы",
    (2, 14): "День святого Валентина",
    (2, 23): "День защитника Отечества",
    (3, 8): "Международный женский день",
    (5, 1): "Праздник Весны и Труда",
    (5, 9): "День Победы",
    (6, 12): "День России",
    (11, 4): "День народного единства",
    (12, 31): "Новый год",
}


# Tone of Voice компании
TONE_OF_VOICE = """
## Главный принцип
Мы говорим с клиентами на одном языке. Без заумностей, без давления.
Это как разговор с компетентным другом — глубоко, но без претенциозности.

## Как надо писать
- Просто и по делу: "Покажем, откуда идут заявки, и как сократить их цену уже в первый месяц."
- С человеческим тоном: "Погружаемся в ваш продукт так, как будто это наш собственный бизнес."
- Вовлекающе: "А теперь представьте: в Google Ads у вас не сливается бюджет, а каждая копейка работает."
- С заботой: "Объясним и покажем всё на понятном языке."
- Экспертно, но легко: "Проверяем гипотезы, строим воронку и связываем с бизнес-результатом."

## Как НЕ надо писать
- Сухо: "Предоставляем услуги настройки контекстной рекламы" ❌
- Канцеляризмы и формальности ❌
- Жаргон без контекста: "зальём кампании, воткнём пиксели" ❌
- Сложные термины без объяснений ❌
- Необоснованные обещания: "гарантируем x10 рост" ❌

## Тональность
- Первый контакт: вежливо, вовлекающе
- Обычное общение: живо, с лёгким юмором, полезно
- При ошибке: честно, без оправданий, с решением
"""


# Глобальный экземпляр настроек
settings = Settings()
