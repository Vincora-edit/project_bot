"""
Обработчики сообщений бота.

Логика:
- Логирование всех сообщений в БД
- Автоматическое назначение проджекта при ответе
- Анализ сообщений клиентов через GPT
- Планирование напоминаний
- Генерация вариантов ответа в личке
"""

from datetime import timedelta, datetime, timezone

from aiogram import Router, types, F

from src.config import settings
from src.core import db, bot
from src.services.openai_service import ai_service
from src.utils.logging import get_logger
from src.utils.time_utils import now_local, parse_timestamp, is_work_time, next_work_start


logger = get_logger(__name__)
router = Router(name="messages")

# Планировщик будет внедрён извне
scheduler = None


def set_scheduler(sched):
    """Устанавливает планировщик для отложенных задач."""
    global scheduler
    scheduler = sched


async def check_for_commitments(message: types.Message, text: str):
    """Проверяет сообщение проджекта на договорённости и создаёт напоминание."""
    try:
        # Получаем контекст
        context = await get_recent_context(str(message.chat.id), int(message.message_id), limit=5)

        # Проверяем через AI
        commitment = await ai_service.extract_commitment(text, context)

        if not commitment:
            return

        # Вычисляем время напоминания
        remind_in_hours = commitment.get("remind_in_hours", 24)
        remind_at = datetime.now(timezone.utc) + timedelta(hours=remind_in_hours)

        # Создаём напоминание
        reminder = db.create_reminder(
            chat_id=str(message.chat.id),
            chat_name=message.chat.title or "Unknown",
            project_id=message.from_user.id,
            reminder_text=commitment.get("text", text[:100]),
            remind_at=remind_at,
            context=text[:500],
            source_message_id=message.message_id
        )

        if reminder:
            logger.info(
                f"Создано напоминание: '{commitment.get('text')}' "
                f"через {remind_in_hours}ч для project_id={message.from_user.id}"
            )

    except Exception as e:
        logger.error(f"Ошибка проверки договорённостей: {e}")


async def log_message(message: types.Message, is_project: bool) -> dict | None:
    """Логирует сообщение в БД."""
    return db.log_message(
        chat_id=str(message.chat.id),
        message_id=message.message_id,
        from_id=message.from_user.id,
        from_name=message.from_user.full_name,
        text=message.text or "",
        chat_name=message.chat.title or "Private",
        is_project=is_project,
    )


async def get_recent_context(chat_id: str, current_message_id: int, limit: int = 5) -> str:
    """Получает последние N сообщений из чата для контекста."""
    messages = db.get_recent_messages(chat_id, current_message_id, limit)

    if not messages:
        return ""

    context_lines = []
    for msg in messages:
        role = "Проджект" if msg.get("is_project") else "Клиент"
        name = msg.get("from_name", "Unknown")
        text = msg.get("text", "")
        context_lines.append(f"{role} ({name}): {text}")

    return "\n".join(context_lines)


async def check_for_answer(log_id: int, chat_id: str, message_id: int, attempt: int):
    """
    Проверяет, был ли ответ на сообщение клиента.

    attempt: 0 -> 15 минут (добавляем suggestion+tasks)
             1 -> 30 минут
             2 -> 60 минут
    """
    logger.info(f"check_for_answer: attempt={attempt}, now={now_local().isoformat()}")

    try:
        msg = db.get_message_by_id(log_id)
        if not msg:
            return

        if msg.get("status") in ("answered", "escalated"):
            return

        # Рабочее время: если нельзя — перенести
        if not is_work_time(now_local()):
            run_at = next_work_start(now_local())
            if scheduler:
                scheduler.add_job(
                    check_for_answer,
                    "date",
                    run_date=run_at,
                    args=[log_id, chat_id, message_id, attempt]
                )
            logger.info(f"Нерабочее время -> перенёс attempt={attempt} на {run_at.isoformat()}")
            return

        # Проверяем: ответил ли проджект после message_id
        answer = db.find_project_answer(chat_id, message_id)

        if answer:
            db.update_message_status(
                log_id,
                status="answered",
                answered_by=answer.get("from_name"),
                answered_message_id=answer.get("message_id"),
                answered_text=answer.get("text", ""),
                answered_at=now_local().isoformat()
            )
            logger.info(f"Ответ найден, закрыли log_id={log_id}")
            return

        # Ответа нет → формируем уведомление
        labels = ["15 минут", "30 минут", "1 час"]
        label = labels[min(attempt, len(labels) - 1)]
        thread_key = msg.get("thread_key") or f"{chat_id}:{message_id}"

        notification_text = (
            f"⏰ Напоминание ({label})\n\n"
            f"🏷️ Чат: {msg.get('chat_name', 'Unknown')}\n"
            f"👤 От: {msg.get('from_name', 'Unknown')}\n"
            f"💬 Сообщение: {msg.get('text', '')}\n"
            f"🔗 Ключ: {thread_key}\n"
        )

        # На первом напоминании добавляем предложение
        if attempt == 0:
            context = await get_recent_context(chat_id, int(message_id), limit=5)
            suggested_reply, tasks = await ai_service.generate_suggestion_and_tasks(
                msg.get("text", ""), context
            )

            tasks_block = "\n".join([f"{i}. {t}" for i, t in enumerate(tasks, 1)])

            notification_text += (
                f"\n🤖 Предложенный ответ:\n{suggested_reply}\n\n"
                f"📝 Задачи:\n{tasks_block}"
            )

        # Отправляем владельцу
        await bot.send_message(settings.owner_id, notification_text)

        # Отправляем проджекту-владельцу чата (если есть и это не владелец)
        owner = db.get_chat_owner(chat_id)
        if owner:
            project_id = int(owner["project_id"])
            if project_id != settings.owner_id:
                await bot.send_message(project_id, notification_text)

        # Планируем следующее напоминание
        next_attempt = attempt + 1
        if next_attempt < len(settings.escalation_delays):
            ts = msg["timestamp"]
            base_time = parse_timestamp(ts)
            base_time = base_time.astimezone(settings.timezone)

            run_at = base_time + timedelta(seconds=settings.escalation_delays[next_attempt])

            if not is_work_time(run_at):
                run_at = next_work_start(run_at)

            db.update_message_status(
                log_id,
                status="waiting",
                pending_until=run_at.isoformat(),
                last_checked_at=now_local().isoformat()
            )

            run_at = run_at.astimezone(settings.timezone)

            if scheduler:
                scheduler.add_job(
                    check_for_answer,
                    "date",
                    run_date=run_at,
                    args=[log_id, chat_id, message_id, next_attempt]
                )

            logger.info(f"Следующее напоминание запланировано на {run_at.isoformat()}")

        else:
            db.update_message_status(
                log_id,
                status="escalated",
                last_checked_at=now_local().isoformat()
            )
            logger.info(f"Финальная эскалация, log_id={log_id}")

    except Exception as e:
        logger.error(f"Ошибка check_for_answer: {e}")


@router.message(F.chat.type == "private")
async def handle_private_message(message: types.Message):
    """Обработка сообщений в личку — генерация вариантов ответа."""
    if message.from_user.id not in settings.project_ids:
        return

    if not message.forward_origin:
        await message.answer("ℹ️ Перешли мне сообщение клиента из чата, и я предложу варианты ответа.")
        return

    client_text = message.text or message.caption or ""

    if not client_text:
        await message.answer("⚠️ Сообщение без текста. Перешли текстовое сообщение.")
        return

    await message.answer("🔄 Генерирую варианты ответа...")

    try:
        fo = message.forward_origin
        original_chat_id = None
        original_message_id = None

        if hasattr(fo, 'chat'):
            original_chat_id = str(fo.chat.id)
        if hasattr(fo, 'message_id'):
            original_message_id = fo.message_id

        context = ""
        if original_chat_id and original_message_id:
            context = await get_recent_context(original_chat_id, original_message_id, limit=10)

        variants = await ai_service.generate_response_variants(client_text, context)

        response_text = (
            f"💬 Сообщение клиента:\n_{client_text}_\n\n"
            f"🤖 Варианты ответа:\n\n"
        )

        for i, variant in enumerate(variants, 1):
            response_text += f"*Вариант {i}:* {variant['tone']}\n{variant['text']}\n\n"

        response_text += "💡 Скопируй подходящий вариант."

        await message.answer(response_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка генерации вариантов: {e}")
        await message.answer("❌ Ошибка генерации. Попробуй ещё раз.")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: types.Message):
    """Обработка текстовых сообщений в групповых чатах."""
    # Только групповые чаты
    if message.chat.type == "private":
        return

    text = (message.text or "").strip()
    if not text:
        return

    user_id = message.from_user.id
    is_project = user_id in settings.project_ids

    # Логируем
    logged = await log_message(message, is_project)
    if not logged:
        return

    # Если проджект — закрепляем (но не владельца)
    if is_project and user_id != settings.owner_id:
        db.upsert_chat_owner(
            str(message.chat.id),
            message.chat.title or "Unknown",
            user_id,
            message.from_user.full_name,
        )

    # Если проджект — проверяем на договорённости
    if is_project:
        await check_for_commitments(message, text)

    # Если НЕ проджект — анализируем (клиент/участник)
    if not is_project:
        context = await get_recent_context(str(message.chat.id), int(message.message_id), limit=5)
        need_answer = await ai_service.check_if_need_answer(text, context)

        if not need_answer:
            db.update_message_status(
                logged["id"],
                status="ignored",
                need_answer=False
            )
            return

        # Нужен ответ: планируем напоминание
        base_time = parse_timestamp(logged["timestamp"])
        base_time = base_time.astimezone(settings.timezone)

        run_at = base_time + timedelta(seconds=settings.escalation_delays[0])

        if not is_work_time(run_at):
            run_at = next_work_start(run_at)

        db.update_message_status(
            logged["id"],
            status="waiting",
            need_answer=True,
            pending_until=run_at.isoformat()
        )

        run_at = run_at.astimezone(settings.timezone)

        if scheduler:
            scheduler.add_job(
                check_for_answer,
                "date",
                run_date=run_at,
                args=[logged["id"], str(message.chat.id), int(message.message_id), 0]
            )

        logger.info(f"1-е напоминание запланировано на {run_at.isoformat()}")
