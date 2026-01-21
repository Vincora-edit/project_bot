"""
Обработчики команд бота.

Команды:
- /start — приветствие
- /help — справка по возможностям
- /botchatid — получить ID чата для Битрикса
- /who — кто ответственный проджект
- /assign — назначить проджекта
- /link — привязать сделку
- /deals — список сделок в чате
- /unlink — отвязать сделку
- /client — база знаний по клиенту (в личке)
- /digest — дайджест по клиенту (в личке)
- /reminders — мои напоминания (в личке)
- /dashboard — ссылка на дашборд (в личке)
- /task — создать задачу в Битрикс24
- /meeting — саммари встречи из видео/аудио (в личке)
"""

from datetime import datetime, timezone, timedelta

import aiohttp

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from src.config import settings
from src.core import db, bot
from src.services import ai_service
from src.services.bitrix_service import bitrix_service
from src.utils.logging import get_logger


logger = get_logger(__name__)
router = Router(name="commands")


# ============ INLINE KEYBOARDS ============

def get_clients_keyboard(chats: list[dict], action: str = "client") -> InlineKeyboardMarkup:
    """Создаёт клавиатуру со списком клиентов."""
    buttons = []
    for chat in chats[:20]:  # Лимит 20 кнопок
        chat_name = chat.get("chat_name", "Без названия")[:30]
        chat_id = chat.get("chat_id")
        buttons.append([
            InlineKeyboardButton(
                text=f"📋 {chat_name}",
                callback_data=f"{action}:{chat_id}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start — приветствие."""
    await message.answer(
        "👋 Привет! Я бот-заботушка 💕\n\n"
        "Слежу за ответами в чатах и помогаю не забывать о важном.\n\n"
        "Напиши /help чтобы узнать, что я умею."
    )


@router.message(Command("help"), F.chat.type == "private")
async def cmd_help(message: types.Message):
    """Команда /help — справка по возможностям бота (только в личке)."""
    help_text = """🤖 *Что я умею:*

*📊 Мониторинг чатов*
Слежу за сообщениями клиентов и напоминаю ответить, если прошло 15/30/60 минут без ответа.

*⏰ Договорённости*
Автоматически распознаю обещания ("завтра пришлю", "сделаю на неделе") и напоминаю о них. Ставлю 👀 на такие сообщения.

*📋 База знаний*
Храню информацию о клиентах: ЛПР, предпочтения, заметки.

*📈 Дайджесты*
Генерирую AI-сводки по переписке с клиентом за период.

*🎊 Праздники*
В праздничные дни предлагаю готовые поздравления для клиентов.

*💡 Допродажи*
1 числа каждого месяца генерирую идеи допродаж.

*📨 Интеграция с Битрикс24*
• Автоматические сообщения при смене стадии сделки
• NPS-опросы после завершения работ
• Отправка актов и счетов клиентам в чат

———————————————

*📝 Команды:*

`/help` — эта справка
`/botchatid` — получить ID чата для Битрикса
`/who` — кто ответственный проджект
`/assign @username` — назначить проджекта
`/link DEAL_ID` — привязать сделку
`/deals` — список сделок в чате
`/unlink DEAL_ID` — отвязать сделку
`/task текст` — создать задачу в Битрикс24

*В личке:*
`/client` — база знаний по клиентам
`/digest` — дайджест по клиенту
`/reminders` — мои напоминания
`/plan` — план-факт по клиенту
`/meeting` — саммари встречи из видео/аудио

*💬 Переслать сообщение*
Перешли мне сообщение клиента в личку — сгенерирую варианты ответа."""

    await message.answer(help_text, parse_mode="Markdown")


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


def _get_chat_list_for_user(project_id: int) -> list[dict]:
    """Получает список чатов, где пользователь — владелец."""
    all_owners = db.get_all_chat_owners()
    # project_id в БД может быть строкой или числом
    return [o for o in all_owners if str(o.get("project_id")) == str(project_id)]


@router.message(Command("client"))
async def cmd_client(message: types.Message, command: CommandObject):
    """
    /client — база знаний по клиенту.

    В личке:
    /client — список всех клиентов
    /client CHAT_ID — информация по клиенту
    /client CHAT_ID lpr Иван — обновить ЛПР

    В групповом чате:
    /client — показать информацию (клиент увидит!)
    /client lpr Иван — обновить ЛПР (клиент увидит!)

    Лучше использовать в личке с ботом!
    """
    if message.from_user.id not in settings.project_ids:
        return

    args = (command.args or "").strip()
    is_private = message.chat.type == "private"

    # Определяем chat_id
    chat_id = None

    if is_private:
        # В личке: первый аргумент может быть chat_id
        if not args:
            # Показываем список клиентов с кнопками
            chats = _get_chat_list_for_user(message.from_user.id)
            if not chats:
                await message.answer(
                    "📋 У тебя пока нет назначенных чатов.\n"
                    "Владелец может назначить тебя командой `/assign`.",
                    parse_mode="Markdown"
                )
                return

            keyboard = get_clients_keyboard(chats, "client")
            await message.answer(
                "📋 *Выбери клиента:*",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        parts = args.split(maxsplit=1)
        first_arg = parts[0]

        # Проверяем, это chat_id или поле
        if first_arg.lstrip("-").isdigit():
            chat_id = first_arg
            args = parts[1] if len(parts) > 1 else ""
        else:
            await message.answer(
                "В личке нужно указать ID чата:\n"
                "`/client CHAT_ID` — просмотр\n"
                "`/client CHAT_ID lpr Иван` — редактирование\n\n"
                "Используй `/client` без аргументов для списка своих чатов.",
                parse_mode="Markdown"
            )
            return
    else:
        # В групповом чате — используем текущий
        chat_id = str(message.chat.id)

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


# Маппинг периодов для дайджеста
DIGEST_PERIODS = {
    "day": (1, "за сегодня"),
    "today": (1, "за сегодня"),
    "week": (7, "за неделю"),
    "month": (30, "за месяц"),
    "3d": (3, "за 3 дня"),
    "7d": (7, "за 7 дней"),
    "14d": (14, "за 14 дней"),
    "30d": (30, "за 30 дней"),
}


@router.message(Command("digest"))
async def cmd_digest(message: types.Message, command: CommandObject):
    """
    /digest [CHAT_ID] [period] — дайджест переписки с клиентом.

    В личке:
    /digest — список клиентов
    /digest CHAT_ID — дайджест за неделю
    /digest CHAT_ID 14d — дайджест за 14 дней

    В групповом чате:
    /digest — за неделю
    /digest 14d — за 14 дней

    Периоды: day, 3d, week, 14d, month
    """
    if message.from_user.id not in settings.project_ids:
        return

    args = (command.args or "").strip()
    is_private = message.chat.type == "private"

    chat_id = None
    period_arg = "week"

    if is_private:
        if not args:
            # Показываем список клиентов с кнопками
            chats = _get_chat_list_for_user(message.from_user.id)
            if not chats:
                await message.answer(
                    "📋 У тебя пока нет назначенных чатов.\n"
                    "Владелец может назначить тебя командой `/assign`.",
                    parse_mode="Markdown"
                )
                return

            keyboard = get_clients_keyboard(chats, "digest")
            await message.answer(
                "📊 *Выбери клиента для дайджеста:*",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        parts = args.split()
        first_arg = parts[0]

        if first_arg.lstrip("-").isdigit() and len(first_arg) > 5:
            # Это chat_id
            chat_id = first_arg
            period_arg = parts[1].lower() if len(parts) > 1 else "week"
        else:
            await message.answer(
                "В личке нужно указать ID чата:\n"
                "`/digest CHAT_ID` — за неделю\n"
                "`/digest CHAT_ID 14d` — за 14 дней\n\n"
                "Используй `/digest` без аргументов для списка своих чатов.",
                parse_mode="Markdown"
            )
            return
    else:
        chat_id = str(message.chat.id)
        period_arg = args.lower() if args else "week"

    # Определяем период
    if period_arg in DIGEST_PERIODS:
        days, period_name = DIGEST_PERIODS[period_arg]
    elif period_arg.replace("d", "").isdigit():
        days = int(period_arg.replace("d", ""))
        period_name = f"за {days} дней"
    else:
        await message.answer(
            "❓ Неизвестный период. Доступные варианты:\n"
            "`/digest day` — за сегодня\n"
            "`/digest 3d` — за 3 дня\n"
            "`/digest week` — за неделю\n"
            "`/digest 14d` — за 2 недели\n"
            "`/digest month` — за месяц",
            parse_mode="Markdown"
        )
        return

    # Отправляем индикатор загрузки
    loading_msg = await message.answer("⏳ Генерирую дайджест...")

    try:
        # Получаем сообщения за период
        since = datetime.now(timezone.utc) - timedelta(days=days)
        messages = db.get_messages_for_period(chat_id, since)

        if not messages:
            await loading_msg.edit_text(
                f"📭 За указанный период ({period_name}) сообщений не найдено."
            )
            return

        # Получаем информацию о клиенте
        client_info = db.get_client_knowledge(chat_id)

        # Генерируем дайджест
        digest = await ai_service.generate_digest(messages, client_info, period_name)

        # Добавляем статистику
        client_messages = sum(1 for m in messages if not m.get("is_project"))
        project_messages = sum(1 for m in messages if m.get("is_project"))

        header = (
            f"📊 *Дайджест {period_name}*\n"
            f"💬 Сообщений: {len(messages)} (клиент: {client_messages}, проджект: {project_messages})\n\n"
        )

        await loading_msg.edit_text(header + digest, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка генерации дайджеста: {e}")
        await loading_msg.edit_text(f"❌ Ошибка генерации дайджеста: {e}")


# ============ CALLBACK HANDLERS ============

async def _show_client_info(chat_id: str, chat_name: str, callback: CallbackQuery):
    """Показывает информацию о клиенте, при необходимости извлекает из переписки."""
    info = db.get_client_knowledge(chat_id)

    # Если базы знаний нет — пробуем извлечь из переписки
    if not info or len([v for v in info.values() if v]) <= 2:  # Только chat_id и timestamps
        await callback.message.edit_text(
            f"📋 *{chat_name}*\n\n⏳ Анализирую переписку...",
            parse_mode="Markdown"
        )

        # Получаем историю за 60 дней
        since = datetime.now(timezone.utc) - timedelta(days=60)
        messages = db.get_messages_for_period(chat_id, since, limit=300)

        if messages:
            extracted = await ai_service.extract_client_info_from_history(messages, chat_name)

            if extracted:
                # Сохраняем в БД
                db.upsert_client_knowledge(chat_id, **extracted)
                info = db.get_client_knowledge(chat_id)

    # Форматируем вывод
    if not info or len([k for k, v in info.items() if v and k not in ("id", "chat_id", "created_at", "updated_at")]) == 0:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Дайджест", callback_data=f"digest:{chat_id}")],
            [InlineKeyboardButton(text="« Назад к списку", callback_data="back:clients")]
        ])
        await callback.message.edit_text(
            f"📋 *{chat_name}*\n\n"
            "ℹ️ Информация о клиенте не найдена.\n"
            "Возможно, переписки ещё мало для анализа.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

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

    lines = [f"📋 *{chat_name}*\n"]

    for field, label in field_labels.items():
        value = info.get(field)
        if value:
            if field == "notes":
                lines.append(f"\n{label}:\n{value}")
            else:
                lines.append(f"{label}: {value}")

    # Кнопки действий
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Дайджест", callback_data=f"digest:{chat_id}")],
        [InlineKeyboardButton(text="« Назад к списку", callback_data="back:clients")]
    ])

    await callback.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data.startswith("client:"))
async def callback_client(callback: CallbackQuery):
    """Обработка нажатия на кнопку клиента."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    chat_id = callback.data.split(":")[1]

    # Получаем название чата
    chats = _get_chat_list_for_user(callback.from_user.id)
    chat_name = "Клиент"
    for chat in chats:
        if chat.get("chat_id") == chat_id:
            chat_name = chat.get("chat_name", "Клиент")
            break

    await callback.answer()
    await _show_client_info(chat_id, chat_name, callback)


@router.callback_query(F.data.startswith("digest:"))
async def callback_digest(callback: CallbackQuery):
    """Обработка нажатия на кнопку дайджеста."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    chat_id = callback.data.split(":")[1]

    await callback.answer()
    await callback.message.edit_text("⏳ Генерирую дайджест за неделю...")

    try:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        messages = db.get_messages_for_period(chat_id, since)

        if not messages:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data=f"client:{chat_id}")]
            ])
            await callback.message.edit_text(
                "📭 За последнюю неделю сообщений не найдено.",
                reply_markup=keyboard
            )
            return

        client_info = db.get_client_knowledge(chat_id)
        digest = await ai_service.generate_digest(messages, client_info, "за неделю")

        client_messages = sum(1 for m in messages if not m.get("is_project"))
        project_messages = sum(1 for m in messages if m.get("is_project"))

        header = (
            f"📊 *Дайджест за неделю*\n"
            f"💬 Сообщений: {len(messages)} (клиент: {client_messages}, проджект: {project_messages})\n\n"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к клиенту", callback_data=f"client:{chat_id}")]
        ])

        await callback.message.edit_text(header + digest, parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка дайджеста: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@router.callback_query(F.data == "back:clients")
async def callback_back_clients(callback: CallbackQuery):
    """Возврат к списку клиентов."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    chats = _get_chat_list_for_user(callback.from_user.id)

    if not chats:
        await callback.answer("Нет клиентов", show_alert=True)
        return

    keyboard = get_clients_keyboard(chats, "client")
    await callback.answer()
    await callback.message.edit_text(
        "📋 *Выбери клиента:*",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ============ НАПОМИНАНИЯ ============

@router.message(Command("reminders"))
async def cmd_reminders(message: types.Message):
    """/reminders — показать активные напоминания (только в личке)."""
    if message.chat.type != "private":
        await message.answer("📩 Эта команда работает только в личке со мной.")
        return

    if message.from_user.id not in settings.project_ids:
        await message.answer("⛔ Нет доступа.")
        return

    reminders = db.get_reminders_for_project(message.from_user.id, status="pending")

    if not reminders:
        await message.answer("📭 У тебя нет активных напоминаний.")
        return

    # Формируем список с кнопками удаления
    text_parts = ["⏰ *Твои активные напоминания:*\n"]

    buttons = []
    for r in reminders[:15]:  # Лимит 15
        chat_name = r.get("chat_name", "Unknown")[:20]
        reminder_text = r.get("reminder_text", "")[:40]
        remind_at = r.get("remind_at", "")

        # Форматируем время
        if remind_at:
            try:
                dt = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
                time_str = dt.strftime("%d.%m %H:%M")
            except:
                time_str = "?"
        else:
            time_str = "?"

        text_parts.append(f"📌 *{chat_name}*")
        text_parts.append(f"   {reminder_text}")
        text_parts.append(f"   🕐 {time_str}\n")

        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {chat_name}: {reminder_text[:20]}",
                callback_data=f"del_reminder:{r['id']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer("\n".join(text_parts), parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data.startswith("del_reminder:"))
async def callback_delete_reminder(callback: CallbackQuery):
    """Удаление напоминания по кнопке."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    reminder_id = int(callback.data.split(":")[1])

    # Удаляем напоминание
    success = db.cancel_reminder(reminder_id)

    if success:
        await callback.answer("✅ Удалено", show_alert=False)

        # Получаем оставшиеся напоминания и обновляем список
        reminders = db.get_reminders_for_project(callback.from_user.id, status="pending")

        if not reminders:
            # Все напоминания удалены
            await callback.message.edit_text("📭 У тебя нет активных напоминаний.")
            return

        # Формируем обновлённый список
        text_parts = ["⏰ *Твои активные напоминания:*\n"]
        buttons = []

        for r in reminders[:15]:
            chat_name = r.get("chat_name", "Unknown")[:20]
            reminder_text = r.get("reminder_text", "")[:40]
            remind_at = r.get("remind_at", "")

            if remind_at:
                try:
                    dt = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
                    time_str = dt.strftime("%d.%m %H:%M")
                except:
                    time_str = "?"
            else:
                time_str = "?"

            text_parts.append(f"📌 *{chat_name}*")
            text_parts.append(f"   {reminder_text}")
            text_parts.append(f"   🕐 {time_str}\n")

            buttons.append([
                InlineKeyboardButton(
                    text=f"❌ {chat_name}: {reminder_text[:20]}",
                    callback_data=f"del_reminder:{r['id']}"
                )
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text("\n".join(text_parts), parse_mode="Markdown", reply_markup=keyboard)
    else:
        await callback.answer("❌ Не удалось удалить", show_alert=True)


# ============ ДАШБОРД ============

@router.message(Command("dashboard"), F.chat.type == "private")
async def cmd_dashboard(message: types.Message):
    """/dashboard — получить ссылку на веб-дашборд (только в личке)."""
    if message.from_user.id not in settings.project_ids:
        await message.answer("⛔ Нет доступа к дашборду.")
        return

    dashboard_api = getattr(settings, 'dashboard_api_url', None)
    bot_secret = getattr(settings, 'dashboard_bot_secret', None)

    if not dashboard_api or not bot_secret:
        await message.answer(
            "📊 *Дашборд НейроПроджект*\n\n"
            "Дашборд ещё не настроен.\n"
            "Скоро здесь будет ссылка для входа!",
            parse_mode="Markdown"
        )
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{dashboard_api}/api/auth/generate-login-link",
                json={
                    "telegramId": message.from_user.id,
                    "userName": message.from_user.full_name,
                    "botSecret": bot_secret,
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success") and data.get("data", {}).get("loginLink"):
                        login_link = data["data"]["loginLink"]
                        await message.answer(
                            "📊 *Дашборд НейроПроджект*\n\n"
                            f"[Войти в дашборд]({login_link})\n\n"
                            "⏳ Ссылка действительна 15 минут.",
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                        return

                await message.answer(
                    "❌ Не удалось получить ссылку на дашборд.\n"
                    "Попробуй позже или обратись к администратору."
                )

    except Exception as e:
        logger.error(f"Ошибка получения ссылки на дашборд: {e}")
        await message.answer(
            "❌ Ошибка соединения с дашбордом.\n"
            "Попробуй позже."
        )


# ============ ПЛАН-ФАКТ ============

@router.message(Command("plan"))
async def cmd_plan(message: types.Message, command: CommandObject):
    """
    /plan — генерация план-факт отчёта по клиенту.

    В личке:
    /plan — список клиентов
    /plan CHAT_ID — сгенерировать план-факт

    В групповом чате:
    /plan — сгенерировать план-факт для этого чата
    """
    if message.from_user.id not in settings.project_ids:
        return

    args = (command.args or "").strip()
    is_private = message.chat.type == "private"

    chat_id = None

    if is_private:
        if not args:
            # Показываем список клиентов с кнопками
            chats = _get_chat_list_for_user(message.from_user.id)
            if not chats:
                await message.answer(
                    "📋 У тебя пока нет назначенных чатов.\n"
                    "Владелец может назначить тебя командой `/assign`.",
                    parse_mode="Markdown"
                )
                return

            keyboard = get_clients_keyboard(chats, "plan")
            await message.answer(
                "📊 *Выбери клиента для план-факта:*",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        # Первый аргумент — chat_id
        if args.lstrip("-").isdigit() and len(args) > 5:
            chat_id = args
        else:
            await message.answer(
                "В личке нужно указать ID чата:\n"
                "`/plan CHAT_ID`\n\n"
                "Используй `/plan` без аргументов для списка своих чатов.",
                parse_mode="Markdown"
            )
            return
    else:
        chat_id = str(message.chat.id)

    # Получаем название чата
    chats = _get_chat_list_for_user(message.from_user.id)
    chat_name = "Клиент"
    for chat in chats:
        if chat.get("chat_id") == chat_id:
            chat_name = chat.get("chat_name", "Клиент")
            break

    # Генерируем план-факт
    await _generate_plan_fact(message, chat_id, chat_name)


async def _generate_plan_fact(message: types.Message, chat_id: str, chat_name: str):
    """Генерирует и отправляет план-факт."""
    loading_msg = await message.answer(
        f"📊 *{chat_name}*\n\n"
        "⏳ Анализирую переписку и генерирую план-факт...\n"
        "Это может занять до минуты.",
        parse_mode="Markdown"
    )

    try:
        # Определяем текущий месяц
        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")
        prev_month_num = now.month - 1 if now.month > 1 else 12
        prev_year = now.year if now.month > 1 else now.year - 1
        prev_month_start = datetime(prev_year, prev_month_num, 1, tzinfo=timezone.utc)
        prev_month_end = datetime(now.year, now.month, 1, tzinfo=timezone.utc) - timedelta(seconds=1)

        # Получаем сообщения за прошлый месяц
        prev_messages = db.get_messages_for_period(chat_id, prev_month_start, prev_month_end, limit=300)

        # Получаем последние сообщения для контекста
        since_30d = now - timedelta(days=30)
        recent_messages = db.get_messages_for_period(chat_id, since_30d, limit=50)

        # Получаем информацию о клиенте
        client_info = db.get_client_knowledge(chat_id)
        client_name = client_info.get("client_name") if client_info else chat_name

        # Форматируем сообщения для AI
        prev_messages_text = "\n".join([
            f"[{m.get('timestamp', '')[:10]}] {'Менеджер' if m.get('is_project') else 'Клиент'}: {m.get('text', '')[:200]}"
            for m in (prev_messages or [])
        ]) or "Нет данных за прошлый месяц"

        recent_messages_text = "\n".join([
            f"{'Менеджер' if m.get('is_project') else 'Клиент'}: {m.get('text', '')[:150]}"
            for m in (recent_messages or [])[-30:]
        ]) or ""

        month_names = [
            'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
            'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
        ]
        current_month_name = month_names[now.month - 1]
        prev_month_name = month_names[prev_month_num - 1]

        # Генерируем план-факт через AI
        plan_fact = await ai_service.generate_plan_fact(
            client_name=client_name or chat_name,
            client_business=client_info.get("notes") if client_info else None,
            prev_month_name=prev_month_name,
            prev_year=prev_year,
            current_month_name=current_month_name,
            current_year=now.year,
            prev_messages=prev_messages_text,
            recent_messages=recent_messages_text
        )

        if not plan_fact:
            await loading_msg.edit_text(
                f"📊 *{chat_name}*\n\n"
                "❌ Не удалось сгенерировать план-факт.\n"
                "Возможно, недостаточно данных в переписке.",
                parse_mode="Markdown"
            )
            return

        # Отправляем результат
        await loading_msg.edit_text(plan_fact, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка генерации план-факта: {e}")
        import traceback
        traceback.print_exc()
        await loading_msg.edit_text(
            f"📊 *{chat_name}*\n\n"
            f"❌ Ошибка генерации: {e}",
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("plan:"))
async def callback_plan(callback: CallbackQuery):
    """Обработка нажатия на кнопку план-факта."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    chat_id = callback.data.split(":")[1]

    # Получаем название чата
    chats = _get_chat_list_for_user(callback.from_user.id)
    chat_name = "Клиент"
    for chat in chats:
        if chat.get("chat_id") == chat_id:
            chat_name = chat.get("chat_name", "Клиент")
            break

    await callback.answer()

    # Создаём фейковое сообщение для передачи в функцию
    await _generate_plan_fact(callback.message, chat_id, chat_name)


# ============ БИТРИКС24 ЗАДАЧИ ============

# Временное хранилище данных для создания задач (user_id -> task_data)
_pending_tasks: dict = {}


@router.message(Command("task"))
async def cmd_task(message: types.Message, command: CommandObject):
    """
    /task [текст] — создать задачу в Битрикс24.

    Примеры:
    /task Подготовить отчёт для клиента
    /task Позвонить АРС Страхование завтра
    """
    if message.from_user.id not in settings.project_ids:
        return

    if not settings.bitrix_webhook_url:
        await message.answer(
            "❌ Интеграция с Битрикс24 не настроена.\n"
            "Добавьте BITRIX_WEBHOOK_URL в переменные окружения."
        )
        return

    args = (command.args or "").strip()

    if not args:
        await message.answer(
            "📝 *Создание задачи в Битрикс24*\n\n"
            "Использование: `/task Текст задачи`\n\n"
            "Примеры:\n"
            "• `/task Подготовить отчёт для клиента`\n"
            "• `/task Позвонить АРС Страхование`\n"
            "• `/task Отправить КП до пятницы`",
            parse_mode="Markdown"
        )
        return

    # Сохраняем данные задачи для дальнейшего выбора
    user_id = message.from_user.id
    _pending_tasks[user_id] = {
        "title": args,
        "chat_id": str(message.chat.id) if message.chat.type != "private" else None,
        "chat_name": message.chat.title if message.chat.type != "private" else None,
        "telegram_user_id": user_id,  # Для определения постановщика
    }

    # Кнопки выбора: кому назначить
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Себе", callback_data="task_assign:self"),
            InlineKeyboardButton(text="👥 Другому", callback_data="task_assign:other"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_assign:cancel"),
        ]
    ])

    await message.answer(
        f"📝 *Новая задача:*\n"
        f"`{args}`\n\n"
        "Кому назначить?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("task_assign:"))
async def callback_task_assign(callback: CallbackQuery):
    """Обработка выбора ответственного."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    user_id = callback.from_user.id
    action = callback.data.split(":")[1]

    if action == "cancel":
        _pending_tasks.pop(user_id, None)
        await callback.answer("Отменено")
        await callback.message.edit_text("❌ Создание задачи отменено.")
        return

    task_data = _pending_tasks.get(user_id)
    if not task_data:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    await callback.answer()

    if action == "self":
        # Назначаем себе — используем маппинг telegram_id -> bitrix_id
        task_data["assign_to"] = "self"
        task_data["telegram_user_id"] = callback.from_user.id
        await _ask_task_group(callback.message, user_id)

    elif action == "other":
        # Показываем список пользователей Битрикс
        users = await bitrix_service.get_users()

        if not users:
            await callback.message.edit_text(
                "❌ Не удалось получить список пользователей из Битрикс24."
            )
            _pending_tasks.pop(user_id, None)
            return

        buttons = []
        for user in users[:10]:  # Лимит 10
            buttons.append([
                InlineKeyboardButton(
                    text=f"👤 {user['name']}",
                    callback_data=f"task_user:{user['id']}"
                )
            ])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="task_assign:cancel")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(
            f"📝 *Задача:* `{task_data['title']}`\n\n"
            "Выбери ответственного:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )


@router.callback_query(F.data.startswith("task_user:"))
async def callback_task_user(callback: CallbackQuery):
    """Обработка выбора пользователя."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    user_id = callback.from_user.id
    bitrix_user_id = callback.data.split(":")[1]

    task_data = _pending_tasks.get(user_id)
    if not task_data:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    task_data["responsible_id"] = bitrix_user_id
    await callback.answer()
    await _ask_task_group(callback.message, user_id)


async def _ask_task_group(message: types.Message, user_id: int):
    """Спрашиваем про группу/проект."""
    task_data = _pending_tasks.get(user_id)
    if not task_data:
        return

    # Получаем группы из Битрикс
    groups = await bitrix_service.get_groups()

    buttons = [
        [InlineKeyboardButton(text="📁 Без группы", callback_data="task_group:none")]
    ]

    if groups:
        for group in groups[:8]:  # Лимит 8 групп
            buttons.append([
                InlineKeyboardButton(
                    text=f"📂 {group['name'][:30]}",
                    callback_data=f"task_group:{group['id']}"
                )
            ])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="task_assign:cancel")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.edit_text(
        f"📝 *Задача:* `{task_data['title']}`\n\n"
        "Выбери группу/проект (опционально):",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("task_group:"))
async def callback_task_group(callback: CallbackQuery):
    """Обработка выбора группы — переходим к выбору дедлайна."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    user_id = callback.from_user.id
    group_id = callback.data.split(":")[1]

    task_data = _pending_tasks.get(user_id)
    if not task_data:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    # Сохраняем группу
    task_data["group_id"] = group_id if group_id != "none" else None

    await callback.answer()

    # Показываем выбор дня дедлайна
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="task_day:today"),
            InlineKeyboardButton(text="📅 Завтра", callback_data="task_day:tomorrow"),
        ],
        [
            InlineKeyboardButton(text="📅 Через 3 дня", callback_data="task_day:3d"),
            InlineKeyboardButton(text="📅 Через неделю", callback_data="task_day:week"),
        ],
        [
            InlineKeyboardButton(text="🚫 Без дедлайна", callback_data="task_day:none"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_assign:cancel"),
        ]
    ])

    await callback.message.edit_text(
        f"📝 *Задача:* `{task_data['title']}`\n\n"
        "Выбери день дедлайна:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("task_day:"))
async def callback_task_day(callback: CallbackQuery):
    """Обработка выбора дня дедлайна."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    user_id = callback.from_user.id
    day_choice = callback.data.split(":")[1]

    task_data = _pending_tasks.get(user_id)
    if not task_data:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    await callback.answer()

    # Если без дедлайна — сразу создаём задачу
    if day_choice == "none":
        task_data["deadline_day"] = None
        await _create_bitrix_task(callback.message, user_id)
        return

    # Сохраняем день
    task_data["deadline_day"] = day_choice

    # Показываем выбор времени
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🕐 10:00", callback_data="task_time:10"),
            InlineKeyboardButton(text="🕐 12:00", callback_data="task_time:12"),
        ],
        [
            InlineKeyboardButton(text="🕐 14:00", callback_data="task_time:14"),
            InlineKeyboardButton(text="🕐 16:00", callback_data="task_time:16"),
        ],
        [
            InlineKeyboardButton(text="🕐 18:00", callback_data="task_time:18"),
            InlineKeyboardButton(text="🕐 20:00", callback_data="task_time:20"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_assign:cancel"),
        ]
    ])

    day_names = {"today": "сегодня", "tomorrow": "завтра", "3d": "через 3 дня", "week": "через неделю"}
    day_text = day_names.get(day_choice, day_choice)

    await callback.message.edit_text(
        f"📝 *Задача:* `{task_data['title']}`\n"
        f"📅 День: {day_text}\n\n"
        "Выбери время:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("task_time:"))
async def callback_task_time(callback: CallbackQuery):
    """Обработка выбора времени дедлайна."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    user_id = callback.from_user.id
    hour = int(callback.data.split(":")[1])

    task_data = _pending_tasks.get(user_id)
    if not task_data:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    task_data["deadline_hour"] = hour

    await callback.answer()
    await _create_bitrix_task(callback.message, user_id)


async def _create_bitrix_task(message: types.Message, user_id: int):
    """Создаёт задачу в Битрикс24."""
    task_data = _pending_tasks.pop(user_id, None)
    if not task_data:
        return

    await message.edit_text("⏳ Создаю задачу в Битрикс24...")

    # Определяем дедлайн
    deadline = None
    deadline_day = task_data.get("deadline_day")
    deadline_hour = task_data.get("deadline_hour", 18)

    if deadline_day:
        now = datetime.now(timezone.utc)
        if deadline_day == "today":
            deadline = now.replace(hour=deadline_hour, minute=0, second=0, microsecond=0)
        elif deadline_day == "tomorrow":
            deadline = (now + timedelta(days=1)).replace(hour=deadline_hour, minute=0, second=0, microsecond=0)
        elif deadline_day == "3d":
            deadline = (now + timedelta(days=3)).replace(hour=deadline_hour, minute=0, second=0, microsecond=0)
        elif deadline_day == "week":
            deadline = (now + timedelta(days=7)).replace(hour=deadline_hour, minute=0, second=0, microsecond=0)

    # Определяем ответственного
    responsible_id = task_data.get("responsible_id")
    telegram_user_id = task_data.get("telegram_user_id")

    if task_data.get("assign_to") == "self" and telegram_user_id:
        # Используем маппинг Telegram ID -> Bitrix ID
        responsible_id = settings.telegram_to_bitrix.get(telegram_user_id, 1)

    # Определяем постановщика (кто создаёт задачу в Telegram)
    creator_telegram_id = task_data.get("telegram_user_id")
    creator_id = settings.telegram_to_bitrix.get(creator_telegram_id, 1) if creator_telegram_id else 1

    # Создаём описание
    description = ""
    if task_data.get("chat_name"):
        description = f"Создано из чата: {task_data['chat_name']}"

    # Создаём задачу
    result = await bitrix_service.create_task(
        title=task_data["title"],
        description=description,
        responsible_id=responsible_id,
        creator_id=creator_id,
        group_id=task_data.get("group_id"),
        deadline=deadline,
    )

    if result:
        task_id = result.get("id")
        bitrix_domain = settings.bitrix_webhook_url.split("/rest/")[0]
        task_url = f"{bitrix_domain}/company/personal/user/1/tasks/task/view/{task_id}/"

        deadline_text = ""
        if deadline:
            deadline_text = f"\n⏰ Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}"

        await message.edit_text(
            f"✅ *Задача создана!*\n\n"
            f"📝 {task_data['title']}{deadline_text}\n\n"
            f"[Открыть в Битрикс24]({task_url})",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        await message.edit_text(
            "❌ Не удалось создать задачу.\n"
            "Проверьте настройки интеграции с Битрикс24."
        )


@router.callback_query(F.data.startswith("task_from_commit:"))
async def callback_task_from_commitment(callback: CallbackQuery):
    """Создание задачи в Битрикс из договорённости."""
    if callback.from_user.id not in settings.project_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return

    reminder_id = int(callback.data.split(":")[1])

    # Получаем данные напоминания
    reminder = db.get_reminder_by_id(reminder_id)
    if not reminder:
        await callback.answer("Напоминание не найдено", show_alert=True)
        return

    await callback.answer()

    # Сохраняем данные для создания задачи
    user_id = callback.from_user.id
    _pending_tasks[user_id] = {
        "title": reminder.get("reminder_text", "Задача из договорённости"),
        "chat_id": reminder.get("chat_id"),
        "chat_name": reminder.get("chat_name"),
        "from_reminder": True,
    }

    # Спрашиваем про ответственного
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Себе", callback_data="task_assign:self"),
            InlineKeyboardButton(text="👥 Другому", callback_data="task_assign:other"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_assign:cancel"),
        ]
    ])

    await callback.message.edit_text(
        f"📝 *Создание задачи из договорённости*\n\n"
        f"`{reminder.get('reminder_text', '')}`\n\n"
        "Кому назначить?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ============ САММАРИ ВСТРЕЧ ============

# Временное хранилище для ожидания файлов (user_id -> context)
_pending_meeting_files: dict = {}


@router.message(Command("meeting"), F.chat.type == "private")
async def cmd_meeting(message: types.Message, command: CommandObject):
    """
    /meeting [название] — получить саммари встречи из аудио/видео.

    Отправь видео или аудио файл после этой команды.

    Примеры:
    /meeting — саммари без контекста
    /meeting Созвон с клиентом АРС — саммари с контекстом
    """
    if message.from_user.id not in settings.project_ids:
        return

    context = (command.args or "").strip()

    # Сохраняем ожидание файла
    _pending_meeting_files[message.from_user.id] = {
        "context": context,
        "timestamp": datetime.now(timezone.utc),
    }

    await message.answer(
        "🎙 *Саммари встречи*\n\n"
        f"{'📋 Контекст: ' + context + chr(10) + chr(10) if context else ''}"
        "Отправь мне видео или аудиофайл записи встречи.\n\n"
        "Поддерживаемые форматы:\n"
        "• Видео: mp4, mkv, webm, mov\n"
        "• Аудио: mp3, m4a, ogg, wav\n\n"
        "⏳ Ожидаю файл...",
        parse_mode="Markdown"
    )


@router.message(F.chat.type == "private", F.video)
async def handle_video_for_meeting(message: types.Message):
    """Обработка видеофайла для саммари встречи."""
    if message.from_user.id not in settings.project_ids:
        return

    # Проверяем, ожидаем ли файл
    pending = _pending_meeting_files.get(message.from_user.id)
    if not pending:
        return  # Не ожидаем файл

    # Проверяем свежесть ожидания (5 минут)
    if (datetime.now(timezone.utc) - pending["timestamp"]).seconds > 300:
        _pending_meeting_files.pop(message.from_user.id, None)
        return

    context = pending.get("context", "")
    _pending_meeting_files.pop(message.from_user.id, None)

    await _process_meeting_file(message, is_video=True, context=context)


@router.message(F.chat.type == "private", F.video_note)
async def handle_video_note_for_meeting(message: types.Message):
    """Обработка кружочка для саммари встречи."""
    if message.from_user.id not in settings.project_ids:
        return

    pending = _pending_meeting_files.get(message.from_user.id)
    if not pending:
        return

    if (datetime.now(timezone.utc) - pending["timestamp"]).seconds > 300:
        _pending_meeting_files.pop(message.from_user.id, None)
        return

    context = pending.get("context", "")
    _pending_meeting_files.pop(message.from_user.id, None)

    await _process_meeting_file(message, is_video=True, context=context, is_video_note=True)


@router.message(F.chat.type == "private", F.audio)
async def handle_audio_for_meeting(message: types.Message):
    """Обработка аудиофайла для саммари встречи."""
    if message.from_user.id not in settings.project_ids:
        return

    pending = _pending_meeting_files.get(message.from_user.id)
    if not pending:
        return

    if (datetime.now(timezone.utc) - pending["timestamp"]).seconds > 300:
        _pending_meeting_files.pop(message.from_user.id, None)
        return

    context = pending.get("context", "")
    _pending_meeting_files.pop(message.from_user.id, None)

    await _process_meeting_file(message, is_video=False, context=context)


@router.message(F.chat.type == "private", F.voice)
async def handle_voice_for_meeting(message: types.Message):
    """Обработка голосового сообщения для саммари встречи."""
    if message.from_user.id not in settings.project_ids:
        return

    pending = _pending_meeting_files.get(message.from_user.id)
    if not pending:
        return

    if (datetime.now(timezone.utc) - pending["timestamp"]).seconds > 300:
        _pending_meeting_files.pop(message.from_user.id, None)
        return

    context = pending.get("context", "")
    _pending_meeting_files.pop(message.from_user.id, None)

    await _process_meeting_file(message, is_video=False, context=context, is_voice=True)


@router.message(F.chat.type == "private", F.document)
async def handle_document_for_meeting(message: types.Message):
    """Обработка документа (видео/аудио файл) для саммари встречи."""
    if message.from_user.id not in settings.project_ids:
        return

    pending = _pending_meeting_files.get(message.from_user.id)
    if not pending:
        return

    if (datetime.now(timezone.utc) - pending["timestamp"]).seconds > 300:
        _pending_meeting_files.pop(message.from_user.id, None)
        return

    # Проверяем тип файла
    doc = message.document
    if not doc.mime_type:
        return

    video_mimes = ["video/mp4", "video/x-matroska", "video/webm", "video/quicktime"]
    audio_mimes = ["audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav"]

    is_video = doc.mime_type in video_mimes
    is_audio = doc.mime_type in audio_mimes

    if not is_video and not is_audio:
        # Проверяем по расширению
        filename = doc.file_name or ""
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        if ext in ["mp4", "mkv", "webm", "mov", "avi"]:
            is_video = True
        elif ext in ["mp3", "m4a", "ogg", "wav", "opus"]:
            is_audio = True
        else:
            return  # Неподдерживаемый формат

    context = pending.get("context", "")
    _pending_meeting_files.pop(message.from_user.id, None)

    await _process_meeting_file(message, is_video=is_video, context=context, is_document=True)


async def _process_meeting_file(
    message: types.Message,
    is_video: bool,
    context: str = "",
    is_video_note: bool = False,
    is_voice: bool = False,
    is_document: bool = False
):
    """Обрабатывает файл встречи: скачивает, транскрибирует, генерирует саммари."""
    import os
    import tempfile

    from src.services.whisper_service import whisper_service

    # Определяем файл для скачивания
    if is_video_note:
        file = message.video_note
        file_ext = "mp4"
    elif is_voice:
        file = message.voice
        file_ext = "ogg"
    elif is_document:
        file = message.document
        file_ext = (file.file_name or "file").split(".")[-1].lower()
    elif is_video:
        file = message.video
        file_ext = "mp4"
    else:
        file = message.audio
        file_ext = (file.file_name or "audio.mp3").split(".")[-1].lower()

    # Проверяем размер (Telegram Bot API лимит 20 МБ для download)
    file_size_mb = (file.file_size or 0) / 1024 / 1024
    if file_size_mb > 20:
        await message.answer(
            f"❌ Файл слишком большой ({file_size_mb:.1f} МБ).\n\n"
            "Telegram Bot API позволяет скачивать файлы до 20 МБ.\n"
            "Попробуй сжать видео или отправить только аудио."
        )
        return

    # Отправляем статус
    status_msg = await message.answer(
        "⏳ *Обрабатываю файл...*\n\n"
        "1️⃣ Скачиваю файл...",
        parse_mode="Markdown"
    )

    temp_dir = tempfile.mkdtemp()
    file_path = os.path.join(temp_dir, f"meeting.{file_ext}")

    try:
        # 1. Скачиваем файл
        await bot.download(file, destination=file_path)

        await status_msg.edit_text(
            "⏳ *Обрабатываю файл...*\n\n"
            "1️⃣ ✅ Файл скачан\n"
            "2️⃣ Транскрибирую аудио...\n\n"
            f"📁 Размер: {file_size_mb:.1f} МБ",
            parse_mode="Markdown"
        )

        # 2. Транскрибируем
        transcript = await whisper_service.transcribe(file_path, is_video=is_video)

        if not transcript:
            await status_msg.edit_text(
                "❌ Не удалось транскрибировать файл.\n\n"
                "Возможные причины:\n"
                "• Файл повреждён\n"
                "• Нет речи в записи\n"
                "• Проблема с FFmpeg на сервере"
            )
            return

        await status_msg.edit_text(
            "⏳ *Обрабатываю файл...*\n\n"
            "1️⃣ ✅ Файл скачан\n"
            "2️⃣ ✅ Транскрибировано\n"
            "3️⃣ Генерирую саммари...\n\n"
            f"📝 Текст: {len(transcript)} символов",
            parse_mode="Markdown"
        )

        # 3. Генерируем саммари
        summary = await ai_service.generate_meeting_summary(transcript, context)

        # 4. Форматируем результат
        result_parts = ["🎙 *Саммари встречи*\n"]

        if context:
            result_parts.append(f"📋 _{context}_\n")

        result_parts.append(f"\n📝 *Резюме:*\n{summary.get('summary', 'Нет данных')}\n")

        if summary.get("key_points"):
            result_parts.append("\n🔑 *Ключевые тезисы:*")
            for point in summary["key_points"][:7]:
                result_parts.append(f"• {point}")

        if summary.get("decisions"):
            result_parts.append("\n\n✅ *Принятые решения:*")
            for decision in summary["decisions"][:5]:
                result_parts.append(f"• {decision}")

        if summary.get("tasks"):
            result_parts.append("\n\n📌 *Задачи:*")
            for task in summary["tasks"][:10]:
                task_text = task.get("text", "")
                assignee = task.get("assignee")
                deadline = task.get("deadline")
                task_line = f"• {task_text}"
                if assignee:
                    task_line += f" — {assignee}"
                if deadline:
                    task_line += f" (до {deadline})"
                result_parts.append(task_line)

        if summary.get("questions"):
            result_parts.append("\n\n❓ *Открытые вопросы:*")
            for question in summary["questions"][:5]:
                result_parts.append(f"• {question}")

        result_text = "\n".join(result_parts)

        # Разбиваем на части если слишком длинное
        if len(result_text) > 4000:
            # Отправляем первую часть
            await status_msg.edit_text(result_text[:4000], parse_mode="Markdown")
            # Отправляем остаток
            await message.answer(result_text[4000:], parse_mode="Markdown")
        else:
            await status_msg.edit_text(result_text, parse_mode="Markdown")

        # Опционально: отправляем полную транскрипцию как файл
        if len(transcript) > 500:
            transcript_path = os.path.join(temp_dir, "transcript.txt")
            with open(transcript_path, "w", encoding="utf-8") as f:
                f.write(transcript)

            from aiogram.types import FSInputFile
            await message.answer_document(
                FSInputFile(transcript_path, filename="transcript.txt"),
                caption="📄 Полная транскрипция"
            )

    except Exception as e:
        logger.error(f"Ошибка обработки файла встречи: {e}")
        import traceback
        traceback.print_exc()
        await status_msg.edit_text(
            f"❌ Ошибка обработки файла:\n`{e}`",
            parse_mode="Markdown"
        )

    finally:
        # Очищаем временные файлы
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
