"""
Сервис планировщика задач.

Периодические задачи:
- Проверка неактивных чатов (12:00 ежедневно)
- Праздничные поздравления (09:00 в праздники)
- Проверка очереди NPS (каждый час)
- Ежемесячная допродажа (1 числа в 10:00)
"""

from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import settings, HOLIDAYS
from src.core import db, bot
from src.services.openai_service import ai_service
from src.utils.logging import get_logger
from src.utils.time_utils import now_local, is_work_time, is_holiday
from src.webhooks.bitrix import send_to_chat


logger = get_logger(__name__)


class SchedulerService:
    """Сервис периодических задач."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def start(self):
        """Запускает планировщик с периодическими задачами."""
        # Проверка неактивных чатов каждый день в 12:00
        self.scheduler.add_job(
            self.check_inactive_chats_job,
            "cron",
            hour=12,
            minute=0,
            id="inactive_chats_check",
            replace_existing=True
        )
        logger.info("Напоминание о неактивных чатах: 12:00 ежедневно")

        # Проверка праздников каждый день в 09:00
        self.scheduler.add_job(
            self.check_holiday_greetings_job,
            "cron",
            hour=9,
            minute=0,
            id="holiday_greetings_check",
            replace_existing=True
        )
        logger.info("Проверка праздников: 09:00 ежедневно")

        # Проверка очереди NPS каждый час
        self.scheduler.add_job(
            self.check_nps_queue_job,
            "interval",
            hours=1,
            id="nps_queue_check",
            replace_existing=True
        )
        logger.info("Проверка NPS-очереди: каждый час")

        # Ежемесячная допродажа - 1 числа в 10:00
        self.scheduler.add_job(
            self.monthly_upsell_job,
            "cron",
            day=1,
            hour=10,
            minute=0,
            id="monthly_upsell",
            replace_existing=True
        )
        logger.info("Допродажа: 1 числа каждого месяца в 10:00")

        # Проверка напоминаний о договорённостях — каждые 15 минут
        self.scheduler.add_job(
            self.check_reminders_job,
            "interval",
            minutes=15,
            id="reminders_check",
            replace_existing=True
        )
        logger.info("Проверка напоминаний: каждые 15 минут")

        self.scheduler.start()
        logger.info(f"Планировщик запущен, таймзона: {self.scheduler.timezone}")

    def get_scheduler(self) -> AsyncIOScheduler:
        """Возвращает экземпляр планировщика."""
        return self.scheduler

    async def check_inactive_chats_job(self):
        """
        Проверка неактивных чатов в 12:00.
        Напоминаем только если в чате НЕ было сообщений СЕГОДНЯ.
        НЕ запускается в выходные и праздники.
        """
        logger.info(f"Запуск проверки неактивных чатов: {now_local().isoformat()}")

        today = now_local()

        # Не запускаем в выходные
        if today.weekday() >= 5:
            logger.info("Сегодня выходной — пропускаем проверку")
            return

        # Не запускаем в праздники
        if is_holiday(today):
            logger.info(f"Сегодня праздник — пропускаем проверку")
            return

        try:
            chats = db.get_all_chat_owners()
            if not chats:
                logger.info("Нет чатов для проверки")
                return

            today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_iso = today_start.isoformat()

            for chat in chats:
                chat_id = chat.get("chat_id")
                chat_name = chat.get("chat_name", "Unknown")
                project_id = chat.get("project_id")
                if not chat_id or not project_id:
                    continue
                try:
                    # Проверяем: были ли сообщения СЕГОДНЯ
                    messages = db.get_recent_messages(chat_id, 999999999, 1)

                    has_activity_today = False
                    if messages:
                        # Проверяем timestamp последнего сообщения
                        last_msg_ts = messages[0].get("timestamp", "")
                        if last_msg_ts >= today_start_iso:
                            has_activity_today = True

                    if has_activity_today:
                        logger.info(f"{chat_name}: сегодня есть активность")
                        continue

                    # Сегодня нет сообщений — напоминаем
                    logger.info(f"{chat_name}: сегодня нет активности")

                    reminder_text = f"📢 {chat_name}: сегодня ещё не было сообщений. Напиши клиенту о ходе работы."
                    await bot.send_message(int(project_id), reminder_text)
                    if int(project_id) != settings.owner_id:
                        await bot.send_message(settings.owner_id, reminder_text)

                except Exception as e:
                    logger.error(f"Ошибка проверки чата {chat_name}: {e}")
        except Exception as e:
            logger.error(f"Ошибка check_inactive_chats_job: {e}")

    async def check_holiday_greetings_job(self):
        """
        Проверка праздников в 09:00.
        Если сегодня праздник — отправляем проджектам напоминание поздравить клиентов.
        """
        today = now_local()
        today_key = (today.month, today.day)

        if today_key not in HOLIDAYS:
            return

        holiday_name = HOLIDAYS[today_key]
        logger.info(f"Сегодня праздник: {holiday_name}")

        try:
            chats = db.get_all_chat_owners()

            if not chats:
                logger.info("Нет чатов для поздравлений")
                return

            # Группируем чаты по проджектам
            projects_chats: dict[int, list[dict]] = {}
            for chat in chats:
                project_id = chat.get("project_id")
                if project_id:
                    if project_id not in projects_chats:
                        projects_chats[project_id] = []
                    projects_chats[project_id].append(chat)

            # Отправляем каждому проджекту напоминание
            for project_id, project_chats in projects_chats.items():
                try:
                    message_parts = [
                        f"🎊 Эй, сегодня же {holiday_name}!",
                        "",
                        "Самое время написать клиентам что-нибудь тёплое 💌",
                        "Держи готовые тексты — просто скопируй и отправь:",
                        "",
                    ]

                    for chat in project_chats:
                        chat_name = chat.get("chat_name", "Unknown")

                        greeting = await ai_service.generate_holiday_greeting(holiday_name, chat_name)

                        message_parts.append(f"📌 *{chat_name}*")
                        message_parts.append(f"```\n{greeting}\n```")
                        message_parts.append("")

                    message_parts.append("✨ Клиенты точно оценят внимание! Ты молодец 🙌")

                    full_message = "\n".join(message_parts)

                    await bot.send_message(int(project_id), full_message, parse_mode="Markdown")
                    logger.info(f"Праздничное напоминание отправлено проджекту {project_id}")

                except Exception as e:
                    logger.error(f"Ошибка отправки проджекту {project_id}: {e}")

            # Сводка владельцу
            try:
                total_chats = sum(len(c) for c in projects_chats.values())
                owner_message = (
                    f"🎊 С праздником — {holiday_name}!\n\n"
                    f"Напоминания разлетелись по проджектам 🚀\n"
                    f"Всего чатов для поздравления: {total_chats}\n\n"
                    f"Теперь клиенты точно почувствуют заботу 💜"
                )

                await bot.send_message(settings.owner_id, owner_message)
            except Exception as e:
                logger.error(f"Ошибка отправки владельцу: {e}")

            logger.info("Праздничные напоминания отправлены")

        except Exception as e:
            logger.error(f"Ошибка check_holiday_greetings_job: {e}")

    async def check_nps_queue_job(self):
        """Проверка очереди NPS (каждый час)."""
        now = now_local()

        # Не отправляем в нерабочее время и праздники
        if not is_work_time(now) or is_holiday(now):
            return

        try:
            pending = db.get_pending_nps()

            for nps in pending:
                try:
                    message = (
                        "Привет! 👋\n\n"
                        "Мы работаем над качеством сервиса и будем благодарны за обратную связь.\n\n"
                        f"Пройдите короткий опрос (1 минута): {nps.get('nps_link', '')}\n\n"
                        "Спасибо! 💜"
                    )

                    chat_id = nps.get("chat_id")
                    thread_id = nps.get("thread_id")

                    success = await send_to_chat(chat_id, message, thread_id)

                    if success:
                        db.mark_nps_sent(nps["id"])
                        logger.info(f"NPS отправлен в чат {chat_id}")

                except Exception as e:
                    logger.error(f"Ошибка отправки NPS: {e}")

        except Exception as e:
            logger.error(f"Ошибка check_nps_queue_job: {e}")

    async def monthly_upsell_job(self):
        """Ежемесячная задача допродажи - 1 числа."""
        try:
            logger.info("Запуск ежемесячной допродажи...")

            # Получаем все чаты
            chats = db.get_all_chat_owners()

            if not chats:
                logger.info("Нет активных чатов для допродажи")
                return

            for chat in chats:
                try:
                    chat_id = chat.get("chat_id")
                    project_id = chat.get("project_id")
                    chat_name = chat.get("chat_name", "Unknown")

                    if not chat_id or not project_id:
                        continue

                    # Получаем историю чата
                    messages = db.get_recent_messages(chat_id, 999999999, 20)
                    chat_history = "\n".join([
                        f"{'Проджект' if m.get('is_project') else 'Клиент'}: {m.get('text', '')[:100]}"
                        for m in messages
                    ])

                    # Генерируем предложение допродажи
                    deal = {"deal_name": chat_name, "service_type": "geo"}
                    suggestion = await ai_service.generate_upsell_suggestion(deal, chat_history)

                    if suggestion:
                        message = (
                            f"💡 Предложение допродажи\n"
                            f"📋 Клиент: {chat_name}\n\n"
                            f"{suggestion}"
                        )
                        await bot.send_message(int(project_id), message)
                        logger.info(f"Допродажа отправлена проджекту {project_id} для чата {chat_name}")

                except Exception as e:
                    logger.error(f"Ошибка допродажи для чата {chat.get('chat_name')}: {e}")

        except Exception as e:
            logger.error(f"Ошибка monthly_upsell_job: {e}")

    async def check_reminders_job(self):
        """Проверка и отправка напоминаний о договорённостях."""
        now = now_local()

        # Не отправляем в нерабочее время
        if not is_work_time(now):
            return

        try:
            pending = db.get_pending_reminders()

            for reminder in pending:
                try:
                    project_id = reminder.get("project_id")
                    chat_name = reminder.get("chat_name", "Unknown")
                    reminder_text = reminder.get("reminder_text", "")
                    context = reminder.get("context", "")

                    message = (
                        f"⏰ Напоминание о договорённости\n\n"
                        f"🏷️ Чат: {chat_name}\n"
                        f"📝 {reminder_text}\n"
                    )

                    if context:
                        message += f"\n💬 Контекст: _{context[:200]}_"

                    await bot.send_message(int(project_id), message, parse_mode="Markdown")
                    db.mark_reminder_sent(reminder["id"])

                    logger.info(f"Напоминание отправлено проджекту {project_id}: {reminder_text}")

                except Exception as e:
                    logger.error(f"Ошибка отправки напоминания: {e}")

        except Exception as e:
            logger.error(f"Ошибка check_reminders_job: {e}")
