"""
Обработчики команд бота.

Команды:
- /start — приветствие
- /botchatid — получить ID чата для Битрикса
- /who — кто ответственный проджект
- /assign — назначить проджекта
- /link — привязать сделку
- /deals — список сделок в чате
- /unlink — отвязать сделку
- /client — база знаний по клиенту
"""

from datetime import datetime, timezone

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from src.config import settings
from src.core import db, bot
from src.utils.logging import get_logger


logger = get_logger(__name__)
router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start — приветствие."""
    await message.answer(
        "👋 Бот-координатор запущен!\n\n"
        "Я слежу за ответами в чатах."
    )


@router.message(Command("botchatid"))
async def cmd_chatid(message: types.Message):
    """
    /botchatid — получить ID чата и топика для настройки в Битриксе.
    """
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    if message.chat.type == "private":
        await message.answer(
            f"📱 Это личный чат\n\n"
            f"• Chat ID: `{chat_id}`",
            parse_mode="Markdown"
        )
        return

    response = f"📋 *Данные для Битрикса:*\n\n• Chat ID: `{chat_id}`"

    if thread_id:
        response += f"\n• Topic ID: `{thread_id}`"
    else:
        response += "\n• Topic ID: нет (общий чат)"

    response += f"\n\n💡 Скопируй Chat ID в поле сделки в Битриксе"

    await message.answer(response, parse_mode="Markdown")


@router.message(Command("who"))
async def cmd_who(message: types.Message):
    """/who — кто ответственный проджект в чате."""
    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    owner = db.get_chat_owner(str(message.chat.id))

    if not owner:
        await message.answer(
            "👤 Ответственный проджект: *не назначен*\n\n"
            "Назначить: ответь на сообщение проджекта командой `/assign` "
            "или используй `/assign <project_id>`",
            parse_mode="Markdown"
        )
        return

    await message.answer(
        f"👤 Ответственный проджект:\n"
        f"• {owner.get('project_name', 'Unknown')}\n"
        f"• ID: `{owner.get('project_id')}`",
        parse_mode="Markdown"
    )


@router.message(Command("assign"))
async def cmd_assign(message: types.Message, command: CommandObject):
    """/assign — назначить ответственного проджекта."""
    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    # Назначать может только владелец
    if message.from_user.id != settings.owner_id:
        await message.answer("⛔️ Назначать ответственного может только владелец.")
        return

    chat_id = str(message.chat.id)
    chat_name = message.chat.title or "Unknown"

    project_id = None
    project_name = None

    # Вариант 1: /assign в ответ на сообщение человека
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        project_id = u.id
        project_name = u.full_name

    # Вариант 2: /assign <project_id>
    if project_id is None:
        arg = (command.args or "").strip()
        if arg.isdigit():
            project_id = int(arg)

    if project_id is None:
        await message.answer(
            "Как назначить проджекта:\n"
            "1) Ответь на сообщение проджекта командой `/assign`\n"
            "или\n"
            "2) Напиши `/assign <project_id>`",
            parse_mode="Markdown"
        )
        return

    # Защита: не назначаем владельца
    if project_id == settings.owner_id:
        await message.answer("Владельца назначать ответственным не нужно 🙂")
        return

    # Назначаем только тех, кто в PROJECT_IDS
    if project_id not in settings.project_ids:
        await message.answer(
            "Этот пользователь не в списке PROJECT_IDS.\n"
            "Добавь его ID в PROJECT_IDS и перезапусти бота."
        )
        return

    # Если имени нет (когда назначили по id), попробуем получить через chat_member
    if not project_name:
        try:
            member = await bot.get_chat_member(message.chat.id, project_id)
            if member and member.user:
                project_name = member.user.full_name
        except Exception:
            project_name = str(project_id)

    ok = db.upsert_chat_owner(chat_id, chat_name, project_id, project_name or str(project_id))
    if not ok:
        await message.answer("❌ Не смог назначить ответственного (ошибка БД).")
        return

    await message.answer(
        f"✅ Назначен ответственный проджект:\n"
        f"• {project_name}\n"
        f"• ID: `{project_id}`",
        parse_mode="Markdown"
    )


@router.message(Command("link"))
async def cmd_link(message: types.Message, command: CommandObject):
    """
    /link DEAL_ID [SERVICE_TYPE] — привязать текущий чат к сделке в Битрикс.

    Примеры:
    /link 12345 geo
    /link 12345 context
    """
    if message.from_user.id not in settings.project_ids:
        return

    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    args = (command.args or "").strip().split()

    if not args:
        await message.answer(
            "Использование: `/link DEAL_ID [SERVICE_TYPE]`\n\n"
            "Примеры:\n"
            "`/link 12345 geo` — геомаркетинг\n"
            "`/link 12345 context` — контекст\n"
            "`/link 12345 site` — сайт\n"
            "`/link 12345 serm` — SERM",
            parse_mode="Markdown"
        )
        return

    deal_id = args[0]
    service_type = args[1] if len(args) > 1 else "geo"

    chat_id = str(message.chat.id)
    chat_name = message.chat.title or "Unknown"

    thread_id = None
    if message.message_thread_id:
        thread_id = str(message.message_thread_id)

    try:
        deal_data = {
            "deal_id": deal_id,
            "deal_name": chat_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "service_type": service_type,
            "project_id": message.from_user.id,
        }

        existing = db.get_deal(deal_id)
        action = "обновлена" if existing else "привязана"

        db.upsert_deal(deal_data)

        thread_info = f"\n• Топик: `{thread_id}`" if thread_id else ""

        await message.answer(
            f"✅ Сделка {action}!\n\n"
            f"• ID сделки: `{deal_id}`\n"
            f"• Услуга: `{service_type}`\n"
            f"• Чат: {chat_name}{thread_info}",
            parse_mode="Markdown"
        )

        logger.info(f"Сделка {deal_id} привязана к чату {chat_id} (thread: {thread_id})")

    except Exception as e:
        logger.error(f"Ошибка привязки сделки: {e}")
        await message.answer(f"❌ Ошибка привязки: {e}")


@router.message(Command("deals"))
async def cmd_deals(message: types.Message):
    """/deals — показать все привязанные сделки в этом чате."""
    if message.from_user.id not in settings.project_ids:
        return

    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    chat_id = str(message.chat.id)

    try:
        deals = db.get_deals_by_chat(chat_id)

        if not deals:
            await message.answer(
                "📭 К этому чату не привязано ни одной сделки.\n\n"
                "Используй `/link DEAL_ID SERVICE_TYPE` для привязки.",
                parse_mode="Markdown"
            )
            return

        lines = ["📋 *Сделки в этом чате:*\n"]
        for deal in deals:
            thread_info = f" (топик: {deal.get('thread_id')})" if deal.get('thread_id') else ""
            stage = deal.get('current_stage_id', '—')
            lines.append(
                f"• `{deal['deal_id']}` | {deal.get('service_type', '?')} | стадия: {stage}{thread_info}"
            )

        await message.answer("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка получения сделок: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("unlink"))
async def cmd_unlink(message: types.Message, command: CommandObject):
    """/unlink DEAL_ID — отвязать сделку от чата."""
    if message.from_user.id not in settings.project_ids:
        return

    deal_id = (command.args or "").strip()

    if not deal_id:
        await message.answer("Использование: `/unlink DEAL_ID`", parse_mode="Markdown")
        return

    try:
        success = db.delete_deal(deal_id)

        if success:
            await message.answer(f"✅ Сделка `{deal_id}` отвязана.", parse_mode="Markdown")
        else:
            await message.answer(f"⚠️ Сделка `{deal_id}` не найдена.", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка отвязки сделки: {e}")
        await message.answer(f"❌ Ошибка: {e}")


# Маппинг полей для /client команды
CLIENT_FIELDS = {
    "lpr": ("decision_maker", "ЛПР"),
    "contact": ("contact_person", "Контактное лицо"),
    "likes": ("preferences", "Предпочтения"),
    "dislikes": ("dislikes", "Не любит"),
    "style": ("communication_style", "Стиль общения"),
    "time": ("best_contact_time", "Лучшее время связи"),
    "tz": ("timezone", "Часовой пояс"),
    "service": ("service_type", "Тип услуги"),
    "payday": ("payment_day", "День оплаты"),
    "name": ("client_name", "Название клиента"),
}


@router.message(Command("client"))
async def cmd_client(message: types.Message, command: CommandObject):
    """
    /client — база знаний по клиенту.

    Использование:
    /client — показать всю информацию
    /client lpr Иван Петров, директор
    /client contact Мария, маркетолог
    /client likes Детальные отчёты
    /client dislikes Звонки без предупреждения
    /client style Дружеский
    /client time 10:00-12:00
    /client note Уходит в отпуск в августе
    /client payday 15
    """
    if message.from_user.id not in settings.project_ids:
        return

    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    chat_id = str(message.chat.id)
    args = (command.args or "").strip()

    # Если без аргументов — показать информацию
    if not args:
        info = db.get_client_knowledge(chat_id)

        if not info:
            await message.answer(
                "📋 *База знаний по клиенту пуста*\n\n"
                "Доступные команды:\n"
                "`/client name` — название клиента\n"
                "`/client lpr` — ЛПР (кто принимает решения)\n"
                "`/client contact` — контактное лицо\n"
                "`/client likes` — что нравится клиенту\n"
                "`/client dislikes` — что не нравится\n"
                "`/client style` — стиль общения\n"
                "`/client time` — лучшее время для связи\n"
                "`/client service` — тип услуги\n"
                "`/client payday` — день оплаты\n"
                "`/client note` — добавить заметку",
                parse_mode="Markdown"
            )
            return

        # Форматируем вывод
        lines = ["📋 *База знаний по клиенту:*\n"]

        field_labels = {
            "client_name": "🏢 Клиент",
            "decision_maker": "👔 ЛПР",
            "contact_person": "👤 Контакт",
            "preferences": "👍 Нравится",
            "dislikes": "👎 Не нравится",
            "communication_style": "💬 Стиль",
            "timezone": "🌍 Часовой пояс",
            "best_contact_time": "⏰ Лучшее время",
            "service_type": "🛠 Услуга",
            "start_date": "📅 Начало работы",
            "payment_day": "💰 День оплаты",
            "notes": "📝 Заметки",
        }

        for field, label in field_labels.items():
            value = info.get(field)
            if value:
                if field == "notes":
                    lines.append(f"\n{label}:\n{value}")
                else:
                    lines.append(f"{label}: {value}")

        await message.answer("\n".join(lines), parse_mode="Markdown")
        return

    # Парсим команду: field value
    parts = args.split(maxsplit=1)
    field_key = parts[0].lower()

    # Обработка заметок отдельно
    if field_key == "note":
        if len(parts) < 2:
            await message.answer("Использование: `/client note Текст заметки`", parse_mode="Markdown")
            return

        note_text = parts[1]
        timestamp = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
        formatted_note = f"[{timestamp}] {note_text}"

        try:
            success = db.append_client_note(chat_id, formatted_note)
            if success:
                await message.answer("✅ Заметка добавлена!", parse_mode="Markdown")
            else:
                await message.answer("❌ Не удалось добавить заметку.")
        except Exception as e:
            logger.error(f"Ошибка добавления заметки: {e}")
            await message.answer(f"❌ Ошибка: {e}")
        return

    # Обработка остальных полей
    if field_key not in CLIENT_FIELDS:
        await message.answer(
            f"❓ Неизвестное поле: `{field_key}`\n\n"
            "Доступные поля: " + ", ".join(f"`{k}`" for k in CLIENT_FIELDS.keys()) + ", `note`",
            parse_mode="Markdown"
        )
        return

    if len(parts) < 2:
        db_field, label = CLIENT_FIELDS[field_key]
        await message.answer(f"Использование: `/client {field_key} значение`", parse_mode="Markdown")
        return

    value = parts[1]
    db_field, label = CLIENT_FIELDS[field_key]

    # Для payday проверяем, что это число
    if field_key == "payday":
        if not value.isdigit() or not (1 <= int(value) <= 31):
            await message.answer("❌ День оплаты должен быть числом от 1 до 31.")
            return
        value = int(value)

    try:
        success = db.update_client_field(chat_id, db_field, value)
        if success:
            await message.answer(f"✅ {label} обновлено: {value}", parse_mode="Markdown")
        else:
            await message.answer("❌ Не удалось обновить информацию.")
    except Exception as e:
        logger.error(f"Ошибка обновления клиента: {e}")
        await message.answer(f"❌ Ошибка: {e}")
