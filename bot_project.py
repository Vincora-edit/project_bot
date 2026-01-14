# stdlib
import asyncio
import os
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

print("✅ Старт файла дошёл до TZ")
print("✅ ZoneInfo в globals():", "ZoneInfo" in globals())
print("✅ zoneinfo модуль:", __import__("zoneinfo").__file__)
TZ = ZoneInfo("Europe/Moscow")

# third-party
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from openai import OpenAI
from supabase import create_client, Client
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web


# Загрузка переменных окружения
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# Инициализация
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
openai_client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
scheduler = AsyncIOScheduler(timezone=TZ)

# ========== НАСТРОЙКИ ==========
OWNER_ID = 1139575259

PROJECT_IDS = [
    OWNER_ID,        # Артём (владелец)
    760732823,       # Кристина
    717802592,       # Наталья
    4739313341,      # Александр
    5269702355,      # Алексей
    904374872,       # Li
]


# ======= РАБОЧЕЕ ВРЕМЯ КОМПАНИИ =======

# Начало рабочего дня (включительно)
WORK_START = time(10, 0)  # 10:00

# Конец рабочего дня (НЕ включительно)
WORK_END   = time(19, 0)  # до 19:00


# ======= TONE OF VOICE =======
# База знаний для генерации ответов в фирменном стиле
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


# ======= ПРАЗДНИКИ =======
# Формат: (месяц, день): "Название праздника"
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


def is_holiday(dt: datetime) -> bool:
    """Проверяет, является ли день праздником"""
    return (dt.month, dt.day) in HOLIDAYS


# ======= ЛОГИКА НАПОМИНАНИЙ =======

# Задержки напоминаний в секундах:
# 1-е напоминание — через 15 минут
# 2-е напоминание — через 30 минут
# 3-е напоминание — через 1 час
#
# ВАЖНО:
# Задержки считаются СТРОГО от времени сообщения клиента,
# а не от момента предыдущего напоминания
ESCALATION_DELAYS = [
    15 * 60,  # 15 минут
    30 * 60,  # 30 минут
    60 * 60,  # 1 час
]


# ======= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВРЕМЕНИ =======

def now_local() -> datetime:
    """
    Возвращает текущее время в часовом поясе компании.
    Используется для:
    - логирования
    - проверки, рабочее ли сейчас время
    """
    return datetime.now(TZ)


def parse_supabase_timestamp(ts: str) -> datetime:
    """
    Парсит timestamp из Supabase.
    Supabase может вернуть формат с 5 знаками микросекунд (например '2025-12-25T17:17:37.72304+00:00'),
    который Python не понимает напрямую. Нормализуем до 6 знаков.
    """
    import re
    # Заменяем Z на +00:00
    ts = ts.replace("Z", "+00:00")
    # Нормализуем микросекунды до 6 знаков
    # Ищем паттерн: .XXXXX (5 цифр) перед + или - (timezone)
    ts = re.sub(r'\.(\d{1,5})([+-])', lambda m: f'.{m.group(1).ljust(6, "0")}{m.group(2)}', ts)
    return datetime.fromisoformat(ts)


def is_work_time(dt: datetime) -> bool:
    """
    Проверяет, попадает ли указанное время в рабочие часы компании.

    Условия:
    - только будние дни (Пн–Пт)
    - время между WORK_START и WORK_END

    Возвращает:
    - True  → рабочее время
    - False → нерабочее время (ночь или выходной)
    """
    if dt.weekday() >= 5:  # 5 = суббота, 6 = воскресенье
        return False

    t = dt.time()
    return WORK_START <= t < WORK_END


def next_work_start(dt: datetime) -> datetime:
    # если сегодня рабочий день, но вне времени — на сегодня 10:00 (если ещё не прошло) или на следующий рабочий день
    base = dt
    # если время раньше начала
    if base.weekday() < 5 and base.time() < WORK_START:
        return base.replace(hour=WORK_START.hour, minute=WORK_START.minute, second=0, microsecond=0)
    # иначе ищем следующий будний день
    while True:
        base = (base + timedelta(days=1)).replace(hour=WORK_START.hour, minute=WORK_START.minute, second=0, microsecond=0)
        if base.weekday() < 5:
            return base

# ========== ФУНКЦИИ РАБОТЫ С БД ==========

async def log_message(message: types.Message, is_project: bool):
    try:
        thread_key = f"{message.chat.id}:{message.message_id}"

        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chat_name": message.chat.title or "Private",
            "chat_id": str(message.chat.id),
            "message_id": int(message.message_id),
            "thread_key": thread_key,
            "from_id": int(message.from_user.id),
            "from_name": message.from_user.full_name,
            "is_project": is_project,
            "project_id": int(message.from_user.id) if is_project else None,
            "text": message.text or "",
            "status": "logged",
        }

        result = supabase.table("chat_log").insert(data).execute()
        return result.data[0] if result.data else None

    except Exception as e:
        # если дубль по thread_key — просто игнорим
        if "duplicate key value" in str(e).lower() or "23505" in str(e):
            return None
        print(f"❌ Ошибка логирования: {e}")
        return None

async def get_recent_context(chat_id: str, current_message_id: int, limit: int = 5) -> str:
    """
    Получает последние N сообщений из чата для контекста.
    Возвращает отформатированную строку с историей.
    """
    try:
        messages = (
            supabase.table("chat_log")
            .select("from_name, text, is_project, timestamp")
            .eq("chat_id", chat_id)
            .lt("message_id", current_message_id)  # До текущего сообщения
            .order("message_id", desc=True)
            .limit(limit)
            .execute()
        )
        
        if not messages.data:
            return ""
        
        # Разворачиваем (самые старые первыми)
        messages.data.reverse()
        
        context_lines = []
        for msg in messages.data:
            role = "Проджект" if msg.get("is_project") else "Клиент"
            name = msg.get("from_name", "Unknown")
            text = msg.get("text", "")
            context_lines.append(f"{role} ({name}): {text}")
        
        return "\n".join(context_lines)
        
    except Exception as e:
        print(f"❌ Ошибка get_recent_context: {e}")
        return ""


async def assign_project_to_chat(chat_id: str, project_id: int, project_name: str, chat_name: str):

    try:
        existing = supabase.table("chat_owners").select("*").eq("chat_id", chat_id).execute()
        
        if not existing.data:
            data = {
                "chat_id": chat_id,
                "chat_name": chat_name,
                "project_id": project_id,
                "project_name": project_name,
                "assigned_at": datetime.now().isoformat()
            }
            supabase.table("chat_owners").insert(data).execute()
            print(f"✅ Проджект {project_name} закреплён за чатом {chat_name}")
            
    except Exception as e:
        print(f"❌ Ошибка закрепления: {e}")


async def check_if_need_answer(text: str, context: str = "") -> bool:
    """Проверка через GPT - нужен ли ответ (с учётом контекста)"""
    try:
        user_content = text or ""
        
        # Если есть контекст - добавляем
        if context:
            user_content = f"Контекст (предыдущие сообщения):\n{context}\n\nНовое сообщение клиента:\n{text}"
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Определи, нужно ли отвечать на НОВОЕ сообщение клиента, учитывая контекст.\n"
                        "Верни ТОЛЬКО 1 или 0:\n"
                        "1 — если это вопрос/просьба/проблема/продолжение темы\n"
                        "0 — если это просто 'ок', 'спасибо', эмодзи\n\n"
                        "ВАЖНО: Если клиент пишет несколько сообщений подряд (развивая мысль) - это ОДНА просьба, "
                        "нужен ответ только на ПОСЛЕДНЕЕ сообщение в цепочке."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            max_tokens=1,
        )
        
        result = (response.choices[0].message.content or "").strip()
        return result == "1"
        
    except Exception as e:
        print(f"❌ Ошибка GPT: {e}")
        return False

async def generate_suggestion_and_tasks(client_text: str, context: str = "") -> tuple[str, list[str]]:
    """Генерирует предложенный ответ и задачи (с учётом контекста)"""
    try:
        user_content = client_text or ""
        
        if context:
            user_content = f"Контекст диалога:\n{context}\n\nПоследнее сообщение клиента:\n{client_text}"
        
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты ассистент проджекта маркетингового агентства. "
                        "Учитывай ВЕСЬ контекст диалога.\n\n"
                        f"ОБЯЗАТЕЛЬНО пиши в нашем tone of voice:\n{TONE_OF_VOICE}\n\n"
                        "Сгенерируй:\n"
                        "1) REPLY — ответ клиенту (1-3 предложения, живо и по-дружески)\n"
                        "2) TASKS — 3-5 конкретных задач для команды\n\n"
                        "Формат:\n"
                        "REPLY: <текст>\n"
                        "TASKS:\n"
                        "- <задача 1>\n"
                        "- <задача 2>"
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            max_tokens=300,
        )
        
        text = (resp.choices[0].message.content or "").strip()
        
        # Парсинг как раньше
        reply = ""
        tasks: list[str] = []
        
        if "REPLY:" in text:
            part = text.split("REPLY:", 1)[1]
            if "TASKS:" in part:
                reply_part, tasks_part = part.split("TASKS:", 1)
                reply = reply_part.strip()
                for line in tasks_part.splitlines():
                    line = line.strip()
                    if line.startswith("-"):
                        tasks.append(line.lstrip("-").strip())
            else:
                reply = part.strip()
        
        if not reply:
            reply = "Спасибо за информацию! Принял, сейчас обработаем и вернёмся."
        if not tasks:
            tasks = ["Обработать запрос клиента", "Проверить детали", "Вернуться с ответом"]
        
        return reply, tasks
        
    except Exception as e:
        print(f"❌ Ошибка GPT suggest: {e}")
        return ("Спасибо! Принял.", ["Обработать запрос"])

async def generate_response_variants(client_text: str, context: str = "") -> list[dict]:
    """
    Генерирует 3 варианта ответа клиенту с разными тонами.
    Возвращает: [
        {"tone": "Дружелюбный", "text": "..."},
    ]
    """
    try:
        user_content = client_text or ""
        
        if context:
            user_content = f"Контекст диалога:\n{context}\n\nСообщение клиента:\n{client_text}"
        
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты помощник проджект-менеджера маркетингового агентства.\n\n"
                        f"ОБЯЗАТЕЛЬНО пиши в нашем tone of voice:\n{TONE_OF_VOICE}\n\n"
                        "Сгенерируй вариант ответа клиенту.\n"
                        "Ответ должен:\n"
                        "- Отвечать на запрос клиента\n"
                        "- Быть готовым к отправке (без правок)\n"
                        "- Быть живым, как разговор с другом\n"
                        "- Содержать конкретные действия или сроки если уместно\n\n"
                        "Формат ответа:\n"
                        "ДРУЖЕЛЮБНЫЙ:\n<текст>\n\n"
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            max_tokens=500,
        )
        
        text = (resp.choices[0].message.content or "").strip()
        
        # Парсим варианты
        variants = []
        
        tones = ["ДРУЖЕЛЮБНЫЙ"]
        tone_names = ["Дружелюбный"]
        
        for i, tone in enumerate(tones):
            if tone + ":" in text:
                # Находим текст между текущим тоном и следующим (или концом)
                parts = text.split(tone + ":", 1)
                if len(parts) > 1:
                    variant_text = parts[1]
                    
                    # Обрезаем до следующего тона
                    for next_tone in tones[i+1:]:
                        if next_tone + ":" in variant_text:
                            variant_text = variant_text.split(next_tone + ":")[0]
                            break
                    
                    variant_text = variant_text.strip()
                    
                    if variant_text:
                        variants.append({
                            "tone": tone_names[i],
                            "text": variant_text
                        })
        
        # Фолбэк если парсинг не сработал
        if len(variants) < 3:
            variants = [
                {"tone": "Дружелюбный", "text": "Привет! Спасибо что написал, уже смотрю. Скоро отпишусь с результатами 😊"},
            ]
        
        return variants
        
    except Exception as e:
        print(f"❌ Ошибка generate_response_variants: {e}")
        return [
            {"tone": "Дружелюбный", "text": "Привет! Спасибо что написал, уже работаю над этим 😊"},
        ]


async def generate_holiday_greeting(holiday_name: str, client_name: str, chat_name: str) -> str:
    """
    Генерирует персонализированное поздравление клиента с праздником.
    Использует название чата для понимания сферы деятельности клиента.
    """
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты помощник проджект-менеджера маркетингового агентства.\n"
                        "Сгенерируй короткое (2-4 предложения) тёплое поздравление клиента с праздником.\n\n"
                        f"ОБЯЗАТЕЛЬНО пиши в нашем tone of voice:\n{TONE_OF_VOICE}\n\n"
                        "Требования:\n"
                        "- Упомяни название праздника\n"
                        "- Если из названия чата понятна сфера клиента — добавь пожелание про его бизнес\n"
                        "- Пиши как другу, но профессионально\n"
                        "- Можно 1-2 эмодзи\n"
                        "- НЕ начинай с 'Уважаемый' — это формально\n"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Праздник: {holiday_name}\nКлиент/чат: {chat_name}",
                },
            ],
            max_tokens=200,
        )

        greeting = (resp.choices[0].message.content or "").strip()
        return greeting

    except Exception as e:
        print(f"❌ Ошибка generate_holiday_greeting: {e}")
        # Фолбэк — универсальное поздравление
        return f"🎉 Поздравляем с праздником — {holiday_name}! Желаем успехов, вдохновения и отличного настроения!"


async def check_for_answer(log_id: int, chat_id: str, message_id: int, attempt: int):
    """
    attempt: 0 -> 15 минут (тут добавляем suggestion+tasks)
             1 -> 30 минут
             2 -> 60 минут
    """
    print(f"🚀 check_for_answer вызван! attempt={attempt}, сейчас (МСК): {now_local().isoformat()}")
    
    try:
        # Берём исходное сообщение клиента
        client_msg_res = supabase.table("chat_log").select("*").eq("id", log_id).execute()
        if not client_msg_res.data:
            return

        msg = client_msg_res.data[0]

        # Если уже есть отметка, что отвечено/эскалировано — можно не дёргаться
        if msg.get("status") in ("answered", "escalated"):
            return

        # Рабочее время: если сейчас нельзя — перенести ту же попытку на ближайшее рабочее
        if not is_work_time(now_local()):
            run_at = next_work_start(now_local())
            scheduler.add_job(
                check_for_answer,
                "date",
                run_date=run_at,
                args=[log_id, chat_id, message_id, attempt]
            )
            print(f"🌙 Нерабочее время → перенёс attempt={attempt} на {run_at.isoformat()}")
            return

        # Проверяем: ответил ли проджект после message_id
        answers = (
            supabase.table("chat_log").select("*")
            .eq("chat_id", chat_id)
            .eq("is_project", True)
            .gt("message_id", int(message_id))
            .order("message_id", desc=False)
            .limit(1)
            .execute()
        )

        if answers.data:
            answer = answers.data[0]
            supabase.table("chat_log").update({
                "status": "answered",
                "answered_by": answer.get("from_name"),
                "answered_message_id": answer.get("message_id"),
                "answered_text": answer.get("text", ""),
                "answered_at": now_local().isoformat()
            }).eq("id", log_id).execute()

            print(f"✅ Ответ найден, закрыли log_id={log_id}")
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

        # ✅ ТОЛЬКО на первой эскалации добавляем suggestion+tasks
        # На первом напоминании добавляем предложение
        if attempt == 0:
            # Получаем контекст последних сообщений
            context = await get_recent_context(chat_id, int(message_id), limit=5)
            
            suggested_reply, tasks = await generate_suggestion_and_tasks(msg.get("text", ""), context)
            
            tasks_block = "\n".join([f"{i}. {t}" for i, t in enumerate(tasks, 1)])
            
            notification_text += (
                f"\n🤖 Предложенный ответ:\n{suggested_reply}\n\n"
                f"📝 Задачи:\n{tasks_block}"
            )

        # тебе
        await bot.send_message(OWNER_ID, notification_text)

        # проджекту-владельцу чата (если есть и это не ты)
        owner = supabase.table("chat_owners").select("*").eq("chat_id", chat_id).execute()
        if owner.data:
            project_id = int(owner.data[0]["project_id"])
            if project_id != OWNER_ID:
                await bot.send_message(project_id, notification_text)

        # Планируем следующее напоминание (attempt+1) — строго от timestamp клиента
        next_attempt = attempt + 1
        if next_attempt < len(ESCALATION_DELAYS):

            ts = msg["timestamp"]

            base_time = parse_supabase_timestamp(ts)
            base_time = base_time.astimezone(TZ)  # Конвертируем UTC → МСК

            run_at = base_time + timedelta(seconds=ESCALATION_DELAYS[next_attempt])

            print(f"🕒 Текущее время (МСК): {now_local().isoformat()}")
            print(f"📨 Время сообщения клиента: {base_time.isoformat()}")
            print(f"🔁 Следующее напоминание запланировано на {run_at.isoformat()}")

            if not is_work_time(run_at):
                run_at = next_work_start(run_at)
                print(f"🌙 Вне рабочего времени → перенёс на {run_at.isoformat()}")

            supabase.table("chat_log").update({
                "status": "waiting",
                "pending_until": run_at.isoformat(),
                "last_checked_at": now_local().isoformat()
            }).eq("id", log_id).execute()

            run_at = run_at.astimezone(TZ)
            print(f"✅ В планировщик передаю (МСК): {run_at.isoformat()}")

            scheduler.add_job(
                check_for_answer,
                "date",
                run_date=run_at,
                args=[log_id, chat_id, message_id, next_attempt]
            )

            print(f"✅ Напоминание успешно добавлено в планировщик")

        else:
            supabase.table("chat_log").update({
                "status": "escalated",
                "last_checked_at": now_local().isoformat()
            }).eq("id", log_id).execute()

            print(f"🔥 Достигнута финальная эскалация, log_id={log_id}")

    except Exception as e:
        print(f"❌ Ошибка check_for_answer: {e}")


# ========== Хелперы для Supabase ===========

async def get_chat_owner(chat_id: str):
    """Возвращает запись владельца чата из chat_owners или None"""
    try:
        res = (
            supabase.table("chat_owners")
            .select("*")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"❌ Ошибка get_chat_owner: {e}")
        return None


async def upsert_chat_owner(chat_id: str, chat_name: str, project_id: int, project_name: str):
    """Создаёт или обновляет владельца чата"""
    try:
        existing = (
            supabase.table("chat_owners")
            .select("*")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        )

        payload = {
            "chat_id": chat_id,
            "chat_name": chat_name,
            "project_id": project_id,
            "project_name": project_name,
            "assigned_at": datetime.now().isoformat(),
        }

        if existing.data:
            supabase.table("chat_owners").update(payload).eq("chat_id", chat_id).execute()
        else:
            supabase.table("chat_owners").insert(payload).execute()

        return True
    except Exception as e:
        print(f"❌ Ошибка upsert_chat_owner: {e}")
        return False

# ========== ОБРАБОТЧИКИ ==========

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Бот-координатор запущен!\n\n"
        "Я слежу за ответами в чатах."
    )



@dp.message(Command("chatid"))
async def cmd_chatid(message: types.Message):
    """
    /chatid — получить ID чата и топика для настройки в Битриксе
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


@dp.message(Command("who"))
async def cmd_who(message: types.Message):
    # Только групповые чаты
    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    owner = await get_chat_owner(str(message.chat.id))

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

@dp.message(Command("assign"))
async def cmd_assign(message: types.Message, command: CommandObject):
    # Только групповые чаты
    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    # Назначать может только владелец
    if message.from_user.id != OWNER_ID:
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
    if project_id == OWNER_ID:
        await message.answer("Владельца назначать ответственным не нужно 🙂")
        return

    # Назначаем только тех, кто в PROJECT_IDS
    if project_id not in PROJECT_IDS:
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

    ok = await upsert_chat_owner(chat_id, chat_name, project_id, project_name or str(project_id))
    if not ok:
        await message.answer("❌ Не смог назначить ответственного (ошибка БД).")
        return

    await message.answer(
        f"✅ Назначен ответственный проджект:\n"
        f"• {project_name}\n"
        f"• ID: `{project_id}`",
        parse_mode="Markdown"
    )


@dp.message(Command("link"))
async def cmd_link(message: types.Message, command: CommandObject):
    """
    /link DEAL_ID [SERVICE_TYPE] — привязать текущий чат к сделке в Битрикс

    Примеры:
    /link 12345 geo
    /link 12345 context
    /link 12345 site

    Только для проджектов в групповых чатах.
    """
    if message.from_user.id not in PROJECT_IDS:
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
    service_type = args[1] if len(args) > 1 else "geo"  # По умолчанию geo

    chat_id = str(message.chat.id)
    chat_name = message.chat.title or "Unknown"

    # Проверяем, есть ли топик (message_thread_id)
    thread_id = None
    if message.message_thread_id:
        thread_id = str(message.message_thread_id)

    try:
        # Проверяем, существует ли уже эта сделка
        existing = supabase.table("deals").select("*").eq("deal_id", deal_id).execute()

        deal_data = {
            "deal_id": deal_id,
            "deal_name": chat_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "service_type": service_type,
            "project_id": message.from_user.id,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        if existing.data:
            # Обновляем
            supabase.table("deals").update(deal_data).eq("deal_id", deal_id).execute()
            action = "обновлена"
        else:
            # Создаём
            deal_data["created_at"] = datetime.now(timezone.utc).isoformat()
            supabase.table("deals").insert(deal_data).execute()
            action = "привязана"

        thread_info = f"\n• Топик: `{thread_id}`" if thread_id else ""

        await message.answer(
            f"✅ Сделка {action}!\n\n"
            f"• ID сделки: `{deal_id}`\n"
            f"• Услуга: `{service_type}`\n"
            f"• Чат: {chat_name}{thread_info}",
            parse_mode="Markdown"
        )

        print(f"🔗 Сделка {deal_id} привязана к чату {chat_id} (thread: {thread_id})")

    except Exception as e:
        print(f"❌ Ошибка привязки сделки: {e}")
        await message.answer(f"❌ Ошибка привязки: {e}")


@dp.message(Command("deals"))
async def cmd_deals(message: types.Message):
    """
    /deals — показать все привязанные сделки в этом чате
    """
    if message.from_user.id not in PROJECT_IDS:
        return

    if message.chat.type == "private":
        await message.answer("Команда работает только в групповых чатах.")
        return

    chat_id = str(message.chat.id)

    try:
        deals = supabase.table("deals").select("*").eq("chat_id", chat_id).execute()

        if not deals.data:
            await message.answer("📭 К этому чату не привязано ни одной сделки.\n\nИспользуй `/link DEAL_ID SERVICE_TYPE` для привязки.", parse_mode="Markdown")
            return

        lines = ["📋 *Сделки в этом чате:*\n"]
        for deal in deals.data:
            thread_info = f" (топик: {deal.get('thread_id')})" if deal.get('thread_id') else ""
            stage = deal.get('current_stage_id', '—')
            lines.append(
                f"• `{deal['deal_id']}` | {deal.get('service_type', '?')} | стадия: {stage}{thread_info}"
            )

        await message.answer("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        print(f"❌ Ошибка получения сделок: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("unlink"))
async def cmd_unlink(message: types.Message, command: CommandObject):
    """
    /unlink DEAL_ID — отвязать сделку от чата
    """
    if message.from_user.id not in PROJECT_IDS:
        return

    deal_id = (command.args or "").strip()

    if not deal_id:
        await message.answer("Использование: `/unlink DEAL_ID`", parse_mode="Markdown")
        return

    try:
        result = supabase.table("deals").delete().eq("deal_id", deal_id).execute()

        if result.data:
            await message.answer(f"✅ Сделка `{deal_id}` отвязана.", parse_mode="Markdown")
        else:
            await message.answer(f"⚠️ Сделка `{deal_id}` не найдена.", parse_mode="Markdown")

    except Exception as e:
        print(f"❌ Ошибка отвязки сделки: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.chat.type == "private")
async def handle_private_message(message: types.Message):
    """Обработка сообщений в личку"""
    
    print(f"🔍 Получено сообщение в ЛС от {message.from_user.id}")
    print(f"🔍 forward_origin: {message.forward_origin}")
    print(f"🔍 Текст: {message.text}")
    
    if message.from_user.id not in PROJECT_IDS:
        print(f"⛔ Не проджект")
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
        
        variants = await generate_response_variants(client_text, context)
        
        response_text = (
            f"💬 Сообщение клиента:\n_{client_text}_\n\n"
            f"🤖 Варианты ответа:\n\n"
        )
        
        for i, variant in enumerate(variants, 1):
            response_text += f"*Вариант {i}:* {variant['tone']}\n{variant['text']}\n\n"
        
        response_text += "💡 Скопируй подходящий вариант."
        
        await message.answer(response_text, parse_mode="Markdown")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        print(traceback.format_exc())
        await message.answer("❌ Ошибка генерации. Попробуй ещё раз.")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: types.Message):
    # Только групповые чаты
    if message.chat.type == "private":
        return  # ← ЭТО УЖЕ ЕСТЬ, но проверь что оно работает

    # ⛔️ НЕТ ТЕКСТА — НЕ ОБРАБАТЫВАЕМ
    text = (message.text or "").strip()
    if not text:
        return

    user_id = message.from_user.id  # int
    is_project = user_id in PROJECT_IDS

    # Логируем
    logged = await log_message(message, is_project)
    if not logged:
        return

    # Если проджект — закрепляем, но НЕ владельца
    if is_project and user_id != OWNER_ID:
        await assign_project_to_chat(
            str(message.chat.id),
            user_id,  # int
            message.from_user.full_name,
            message.chat.title or "Unknown",
        )

    # Если НЕ проджект — анализируем (клиент/обычный участник)
    # Если НЕ проджект — анализируем (клиент/обычный участник)
    if not is_project:
    # Получаем контекст последних 5 сообщений
        context = await get_recent_context(str(message.chat.id), int(message.message_id), limit=5)
    
        need_answer = await check_if_need_answer(text, context)

        if not need_answer:
            supabase.table("chat_log").update({
                "need_answer": False,
                "status": "ignored"
            }).eq("id", logged["id"]).execute()
            return
        print(f"🔍 DEBUG logged timestamp: {logged['timestamp']}")
        print(f"🔍 DEBUG тип: {type(logged['timestamp'])}")
        # --- НУЖЕН ОТВЕТ: ставим напоминание (15/30/60 от времени сообщения) ---
        # Время сообщения берём из БД (logged["timestamp"]) — единая точка истины
        base_time = parse_supabase_timestamp(logged["timestamp"])
        base_time = base_time.astimezone(TZ)  # Конвертируем UTC → МСК

        # 1-е напоминание: +1 секунда от base_time (для теста)
        run_at = base_time + timedelta(seconds=ESCALATION_DELAYS[0])

        print(f"🔍 ОТЛАДКА:")
        print(f"  Время сообщения (base_time): {base_time.isoformat()}")
        print(f"  Текущее время (МСК): {now_local().isoformat()}")
        print(f"  Задержка: {ESCALATION_DELAYS[0]} секунд")
        print(f"  Планируемое время (run_at ДО проверки): {run_at.isoformat()}")
        print(f"  Рабочее время сейчас: {is_work_time(now_local())}")
        print(f"  Рабочее время в run_at: {is_work_time(run_at)}")

        # Если попали в нерабочее время — переносим на ближайшее рабочее
        if not is_work_time(run_at):
            old_run_at = run_at
            run_at = next_work_start(run_at)
            print(f"  🌙 Перенесено с {old_run_at.isoformat()} на {run_at.isoformat()}")

        supabase.table("chat_log").update({
            "need_answer": True,
            "status": "waiting",
            "pending_until": run_at.isoformat()
        }).eq("id", logged["id"]).execute()

        run_at = run_at.astimezone(TZ)

        print(f"  ✅ ФИНАЛЬНОЕ время для планировщика: {run_at.isoformat()}")
        print(f"  Таймзона планировщика: {scheduler.timezone}")

        # Планируем первую проверку (attempt=0)
        scheduler.add_job(
            check_for_answer,
            "date",
            run_date=run_at,
            args=[logged["id"], str(message.chat.id), int(message.message_id), 0]
        )

        print(f"⏳ 1-е напоминание запланировано на {run_at.isoformat()}")


# ========== ЗАПУСК ==========



# ========== НАПОМИНАНИЕ О НЕАКТИВНЫХ ЧАТАХ ==========

async def check_inactive_chats_job():
    """
    Проверка неактивных чатов в 12:00.

    Логика: напоминаем только если в чате НЕ было сообщений СЕГОДНЯ (до 12:00).
    Если сегодня уже есть активность — всё ок, не дёргаем.

    НЕ запускается в выходные и праздники.
    """
    print(f"🔔 Запуск проверки неактивных чатов: {now_local().isoformat()}")

    today = now_local()

    # Не запускаем в выходные
    if today.weekday() >= 5:  # 5 = суббота, 6 = воскресенье
        print("📅 Сегодня выходной — пропускаем проверку")
        return

    # Не запускаем в праздники
    if is_holiday(today):
        print(f"🎉 Сегодня праздник ({HOLIDAYS[(today.month, today.day)]}) — пропускаем проверку")
        return

    try:
        chats_resp = supabase.table("chat_owners").select("*").execute()
        chats = chats_resp.data or []
        if not chats:
            print("📭 Нет чатов для проверки")
            return

        # Проверяем сообщения СЕГОДНЯ (с начала дня до текущего момента)
        today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_iso = today_start.isoformat()

        print(f"📅 Проверяем сообщения с начала дня: {today_start_iso}")

        for chat in chats:
            chat_id = chat.get("chat_id")
            chat_name = chat.get("chat_name", "Unknown")
            project_id = chat.get("project_id")
            if not chat_id or not project_id:
                continue
            try:
                # Проверяем: были ли сообщения СЕГОДНЯ
                messages_today = (
                    supabase.table("chat_log")
                    .select("id")
                    .eq("chat_id", chat_id)
                    .gte("timestamp", today_start_iso)
                    .limit(1)
                    .execute()
                )

                if messages_today.data:
                    # Сегодня есть активность — всё ок
                    print(f"✅ {chat_name}: сегодня есть активность")
                    continue

                # Сегодня нет сообщений — напоминаем
                print(f"⚠️ {chat_name}: сегодня нет активности")

                reminder_text = f"📢 {chat_name}: сегодня ещё не было сообщений. Напиши клиенту о ходе работы."
                await bot.send_message(int(project_id), reminder_text)
                if int(project_id) != OWNER_ID:
                    await bot.send_message(OWNER_ID, reminder_text)

            except Exception as e:
                print(f"❌ Ошибка проверки чата {chat_name}: {e}")
    except Exception as e:
        print(f"❌ Ошибка check_inactive_chats_job: {e}")


async def check_holiday_greetings_job():
    """
    Проверка праздников в 09:00.
    Если сегодня праздник — отправляем проджектам напоминание поздравить клиентов
    с персонализированными примерами текстов.
    """
    today = now_local()
    today_key = (today.month, today.day)

    if today_key not in HOLIDAYS:
        return

    holiday_name = HOLIDAYS[today_key]
    print(f"🎉 Сегодня праздник: {holiday_name}")

    try:
        # Получаем все чаты с назначенными проджектами
        chats_resp = supabase.table("chat_owners").select("*").execute()
        chats = chats_resp.data or []

        if not chats:
            print("📭 Нет чатов для поздравлений")
            return

        # Группируем чаты по проджектам
        projects_chats: dict[int, list[dict]] = {}
        for chat in chats:
            project_id = chat.get("project_id")
            if project_id:
                if project_id not in projects_chats:
                    projects_chats[project_id] = []
                projects_chats[project_id].append(chat)

        # Отправляем каждому проджекту напоминание с примерами поздравлений
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

                    # Генерируем персонализированное поздравление
                    greeting = await generate_holiday_greeting(holiday_name, "", chat_name)

                    message_parts.append(f"📌 *{chat_name}*")
                    message_parts.append(f"```\n{greeting}\n```")
                    message_parts.append("")

                message_parts.append("✨ Клиенты точно оценят внимание! Ты молодец 🙌")

                full_message = "\n".join(message_parts)

                await bot.send_message(int(project_id), full_message, parse_mode="Markdown")
                print(f"📨 Праздничное напоминание отправлено проджекту {project_id}")

            except Exception as e:
                print(f"❌ Ошибка отправки проджекту {project_id}: {e}")

        # Также отправляем владельцу сводку
        try:
            total_chats = sum(len(c) for c in projects_chats.values())
            owner_message = (
                f"🎊 С праздником — {holiday_name}!\n\n"
                f"Напоминания разлетелись по проджектам 🚀\n"
                f"Всего чатов для поздравления: {total_chats}\n\n"
                f"Теперь клиенты точно почувствуют заботу 💜"
            )

            await bot.send_message(OWNER_ID, owner_message)
        except Exception as e:
            print(f"❌ Ошибка отправки владельцу: {e}")

        print(f"✅ Праздничные напоминания отправлены")

    except Exception as e:
        print(f"❌ Ошибка check_holiday_greetings_job: {e}")


# ========== ИНТЕГРАЦИЯ С БИТРИКС (WEBHOOK) ==========

WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", 8080))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Опционально для безопасности


async def send_to_chat(chat_id: str, message: str, thread_id: str | None = None):
    """Отправка сообщения в чат с учётом топика"""
    try:
        if thread_id:
            await bot.send_message(
                int(chat_id),
                message,
                message_thread_id=int(thread_id)
            )
        else:
            await bot.send_message(int(chat_id), message)
        return True
    except Exception as e:
        print(f"❌ Ошибка отправки в чат {chat_id}: {e}")
        return False


async def send_document_to_chat(chat_id: str, document_url: str, caption: str, thread_id: str | None = None):
    """Отправка документа (PDF) в чат"""
    try:
        if thread_id:
            await bot.send_document(
                int(chat_id),
                document=document_url,
                caption=caption,
                message_thread_id=int(thread_id)
            )
        else:
            await bot.send_document(
                int(chat_id),
                document=document_url,
                caption=caption
            )
        return True
    except Exception as e:
        print(f"❌ Ошибка отправки документа в чат {chat_id}: {e}")
        return False


async def handle_stage_change(request: web.Request) -> web.Response:
    """
    Webhook от Битрикса при смене стадии сделки.

    Параметры (GET или POST):
    - chat_id: ID чата Telegram (обязательно)
    - stage_id: ID стадии в Битриксе (обязательно)
    - deal_id: ID сделки (опционально, для логирования)
    - title: Название сделки (опционально)
    - service: Тип услуги - geo/context/site/serm (опционально, по умолчанию geo)
    - topic_id: ID топика если супергруппа (опционально)
    - secret: Секретный ключ (опционально)
    """
    try:
        # Пробуем получить данные из разных источников
        data = {}

        # 1. GET-параметры из URL
        if request.query:
            data = dict(request.query)
            print(f"📥 Webhook GET: {data}")

        # 2. POST JSON
        elif request.content_type == "application/json":
            data = await request.json()
            print(f"📥 Webhook JSON: {data}")

        # 3. POST form-data
        elif request.content_type == "application/x-www-form-urlencoded":
            post_data = await request.post()
            data = dict(post_data)
            print(f"📥 Webhook FORM: {data}")

        # 4. Пустой запрос — пробуем JSON
        else:
            try:
                data = await request.json()
                print(f"📥 Webhook JSON (fallback): {data}")
            except:
                print(f"⚠️ Не удалось распарсить данные webhook")
                return web.json_response({"status": "error", "message": "Invalid request format"}, status=400)

        # Проверка секрета (если настроен)
        if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
            print("⚠️ Неверный secret в webhook")
            return web.json_response({"status": "error", "message": "Invalid secret"}, status=403)

        # Получаем данные из запроса
        chat_id = data.get("chat_id")
        stage_id = data.get("stage_id")
        deal_id = data.get("deal_id", "unknown")
        title = data.get("title", "")
        service_type = data.get("service", "geo")
        topic_id = data.get("topic_id")
        doc_pdf = data.get("pdf", "")  # Ссылка на документ (акт, счёт)

        # Проверяем обязательные поля
        if not chat_id:
            return web.json_response({"status": "error", "message": "Missing chat_id"}, status=400)

        if not stage_id:
            return web.json_response({"status": "error", "message": "Missing stage_id"}, status=400)

        # Приводим chat_id к строке и очищаем от мусора Битрикса
        # Битрикс может присылать: "-1002480121582 [ http://-1002480121582/ ]"
        chat_id = str(chat_id).strip()
        if " [" in chat_id:
            chat_id = chat_id.split(" [")[0].strip()

        thread_id = str(topic_id).strip() if topic_id else None

        print(f"📍 Обработка: deal={deal_id}, stage={stage_id}, chat={chat_id}, service={service_type}, pdf={doc_pdf}")

        # Логируем в файл для отладки
        with open("/var/log/stage_ids.log", "a") as f:
            f.write(f"stage_id={stage_id}, service={service_type}, deal={deal_id}, pdf={doc_pdf}\n")

        # Если передан pdf — это отправка документа (акт/счёт)
        if doc_pdf:
            # Определяем тип документа по stage_id или параметру
            doc_type = data.get("doc_type", "act")  # act, invoice

            if doc_type == "invoice":
                caption = "Добрый день! Направляю вам счёт на оплату. Если возникнут вопросы — пишите, всё обсудим."
            else:
                caption = "Добрый день! Работы завершены, направляю вам закрывающий акт. Спасибо за сотрудничество, было приятно работать вместе!"

            # Пробуем отправить как документ (файл)
            if doc_pdf.endswith('.pdf') or '/disk/' in doc_pdf:
                success = await send_document_to_chat(chat_id, doc_pdf, caption, thread_id)
                if success:
                    print(f"📄 Документ отправлен как файл в чат {chat_id}: {doc_pdf}")
                    return web.json_response({"status": "ok", "action": "document_sent"})

            # Если не PDF или не удалось — отправляем как сообщение со ссылкой
            message = f"{caption}\n\nСкачать документ: {doc_pdf}"
            success = await send_to_chat(chat_id, message, thread_id)
            if success:
                print(f"📄 Ссылка на документ отправлена в чат {chat_id}: {doc_pdf}")
                return web.json_response({"status": "ok", "action": "document_link_sent"})
            else:
                return web.json_response({"status": "error", "message": "Failed to send document"}, status=500)

        # Находим действия для этой стадии
        actions_resp = supabase.table("stage_actions")\
            .select("*")\
            .eq("stage_id", stage_id)\
            .eq("service_type", service_type)\
            .eq("is_active", True)\
            .order("priority")\
            .execute()

        if not actions_resp.data:
            print(f"ℹ️ Нет действий для стадии {stage_id} / {service_type} - создаю автоматически")

            # Извлекаем название стадии (после слеша)
            stage_name = stage_id.split(" / ")[-1] if " / " in stage_id else stage_id

            # Создаём дефолтный промпт
            default_prompt = f"""Ты - проджект-менеджер (девушка), пишешь клиенту в Telegram. Веди себя как живой человек.

Задача: сообщить клиенту что мы начали работу - "{stage_name}".

Правила:
- Обращайся на "Вы"
- Ты НЕ делаешь работу сама - ты управляешь процессом и информируешь клиента
- Говори "мы начали", "наши специалисты", "команда работает"
- Женский род только когда говоришь о себе (я напишу, я отправлю отчёт)
- Напиши 3-5 предложений: что происходит, зачем это нужно, какой результат будет
- Живой дружелюбный стиль, профессиональный
- Без эмодзи
- НЕ используй слова "проект", "этап", "стадия"

История чата ниже - используй для контекста."""

            # Добавляем запись в базу
            new_action = {
                "stage_id": stage_id,
                "service_type": service_type,
                "action_type": "send_message",
                "message_template": "",
                "use_ai": True,
                "ai_prompt": default_prompt,
                "priority": 1,
                "is_active": True
            }
            supabase.table("stage_actions").insert(new_action).execute()
            print(f"✅ Создана новая запись для стадии: {stage_id}")

            # Используем только что созданное действие
            actions_resp.data = [new_action]

        # Формируем данные сделки для шаблонов
        deal = {
            "deal_id": deal_id,
            "deal_name": title,
            "service_type": service_type,
            "chat_id": chat_id,
            "thread_id": thread_id
        }

        # Выполняем действия
        actions_executed = 0
        for action in actions_resp.data:
            success = await execute_stage_action(action, deal, chat_id, thread_id)
            if success:
                actions_executed += 1

        print(f"✅ Обработано {actions_executed} действий для сделки {deal_id}")
        return web.json_response({"status": "ok", "actions": actions_executed})

    except Exception as e:
        print(f"❌ Ошибка обработки webhook: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def execute_stage_action(action: dict, deal: dict, chat_id: str, thread_id: str | None) -> bool:
    """Выполняет конкретное действие при смене стадии"""

    action_type = action.get("action_type")
    template = action.get("message_template", "")

    try:
        if action_type == "send_message":
            # Автоматическая отправка в чат клиенту
            if action.get("use_ai"):
                # Генерируем через GPT с учётом истории и шаблона
                ai_prompt = action.get("ai_prompt") or "Напиши сообщение клиенту о переходе на новый этап работы."
                message = await generate_ai_stage_message(ai_prompt, deal, template)
            else:
                # Просто подставляем плейсхолдеры в шаблон
                message = format_stage_message(template, deal)

            if not message:
                print(f"⚠️ Пустое сообщение для стадии {action.get('stage_id')}")
                return False

            return await send_to_chat(chat_id, message, thread_id)

        elif action_type == "suggest_message":
            # Предлагаем проджекту, не отправляем автоматом
            if action.get("use_ai"):
                ai_prompt = action.get("ai_prompt") or "Предложи сообщение для клиента."
                message = await generate_ai_stage_message(ai_prompt, deal, template)
            else:
                message = format_stage_message(template, deal)

            project_id = deal.get("project_id")

            if project_id:
                suggestion = (
                    f"💡 Сделка: {deal.get('deal_name', deal.get('deal_id'))}\n"
                    f"📍 Стадия: {action.get('stage_id')}\n\n"
                    f"Предлагаю отправить:\n\n"
                    f"{message}\n\n"
                    f"Отправь сам или отредактируй."
                )
                await bot.send_message(int(project_id), suggestion)
                return True
            return False

        elif action_type == "schedule_nps":
            # Планируем NPS-опрос
            delay_days = action.get("nps_delay_days", 3)
            send_at = datetime.now(timezone.utc) + timedelta(days=delay_days)

            supabase.table("nps_queue").insert({
                "deal_id": deal.get("deal_id"),
                "chat_id": chat_id,
                "thread_id": thread_id,
                "service_type": deal.get("service_type"),
                "send_at": send_at.isoformat(),
                "nps_link": action.get("nps_link", "")
            }).execute()

            print(f"📅 NPS запланирован на {send_at.isoformat()} для сделки {deal.get('deal_id')}")
            return True

        elif action_type == "send_nps":
            # Мгновенная отправка NPS со ссылкой
            nps_link = action.get("nps_link", "https://vincora.ru/nps_first")

            if action.get("use_ai"):
                ai_prompt = action.get("ai_prompt") or f"""Ты - проджект-менеджер (девушка), пишешь клиенту в Telegram.

Задача: попросить клиента оценить работу по ссылке.

Правила:
- Обращайся на "Вы"
- Поблагодари за сотрудничество
- Попроси уделить 1 минуту на оценку
- Скажи что это важно для улучшения работы
- 2-3 предложения, без эмодзи
- В конце ОБЯЗАТЕЛЬНО добавь ссылку: {nps_link}

История чата ниже."""
                message = await generate_ai_stage_message(ai_prompt, deal, template)
                # Убедимся что ссылка есть в сообщении
                if nps_link not in message:
                    message += f"\n\n{nps_link}"
            else:
                message = template or f"Будем благодарны за оценку нашей работы: {nps_link}"

            return await send_to_chat(chat_id, message, thread_id)

        elif action_type == "notify_project":
            # Уведомляем проджекта
            project_id = deal.get("project_id")
            if project_id:
                await bot.send_message(
                    int(project_id),
                    f"📌 {deal.get('deal_name', 'Сделка')}: перешла на стадию {action.get('stage_id')}"
                )
                return True
            return False

        else:
            print(f"⚠️ Неизвестный тип действия: {action_type}")
            return False

    except Exception as e:
        print(f"❌ Ошибка выполнения действия {action_type}: {e}")
        return False


def format_stage_message(template: str, deal: dict) -> str:
    """Подставляет плейсхолдеры в шаблон сообщения"""
    if not template:
        return ""

    return template.format(
        client_name=deal.get("client_name", ""),
        deal_name=deal.get("deal_name", ""),
        service_type=deal.get("service_type", ""),
        deal_id=deal.get("deal_id", "")
    )


async def get_chat_history_for_ai(chat_id: str, limit: int = 15) -> str:
    """Получает историю чата для контекста AI"""
    try:
        messages = (
            supabase.table("chat_log")
            .select("from_name, text, is_project, timestamp")
            .eq("chat_id", chat_id)
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )

        if not messages.data:
            return "История чата пуста."

        # Разворачиваем (старые первыми)
        messages.data.reverse()

        history_lines = []
        for msg in messages.data:
            role = "Проджект" if msg.get("is_project") else "Клиент"
            name = msg.get("from_name", "Unknown")
            text = msg.get("text", "")[:200]  # Обрезаем длинные сообщения
            if text:
                history_lines.append(f"{role} ({name}): {text}")

        return "\n".join(history_lines) if history_lines else "История чата пуста."

    except Exception as e:
        print(f"❌ Ошибка получения истории чата: {e}")
        return "Не удалось загрузить историю."


async def generate_ai_stage_message(prompt: str, deal: dict, template: str = "") -> str:
    """
    Генерирует персонализированное сообщение через GPT.

    Учитывает:
    - Историю переписки в чате
    - Шаблон сообщения (как основу)
    - Данные о сделке
    - Tone of voice компании
    """
    try:
        chat_id = deal.get("chat_id", "")

        # Загружаем историю чата
        chat_history = await get_chat_history_for_ai(chat_id, limit=15)

        # Формируем контекст
        context = f"""
## Данные о сделке
- Название: {deal.get('deal_name', 'Не указано')}
- Услуга: {deal.get('service_type', 'geo')}
- ID сделки: {deal.get('deal_id', '')}

## История переписки с клиентом (последние сообщения)
{chat_history}

## Базовый шаблон сообщения (используй как основу, но адаптируй)
{template if template else 'Шаблон не указан — сгенерируй сообщение с нуля'}
"""

        system_prompt = f"""{TONE_OF_VOICE}

## Твоя задача
{prompt}

## Важные правила
1. Пиши от лица проджект-менеджера агентства
2. Учитывай контекст переписки — если клиент что-то обсуждал, можно это упомянуть
3. Сообщение должно быть коротким (2-4 предложения)
4. Не используй формальные обращения типа "Уважаемый"
5. Можно использовать 1-2 эмодзи, но не перебарщивай
6. Если в истории видно имя клиента — можешь обратиться по имени
"""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            max_tokens=300,
            temperature=0.7
        )

        result = (response.choices[0].message.content or "").strip()

        # Если AI вернул пустоту — используем шаблон
        if not result and template:
            return format_stage_message(template, deal)

        return result

    except Exception as e:
        print(f"❌ Ошибка генерации AI-сообщения: {e}")
        # Фолбэк на шаблон
        if template:
            return format_stage_message(template, deal)
        return ""


async def handle_health(request: web.Request) -> web.Response:
    """Health check эндпоинт"""
    return web.json_response({"status": "ok", "service": "projectbot"})


async def check_nps_queue_job():
    """Проверка очереди NPS (каждый час)"""
    now = now_local()

    # Не отправляем в нерабочее время и праздники
    if not is_work_time(now) or is_holiday(now):
        return

    try:
        # Берём все готовые к отправке NPS
        pending = supabase.table("nps_queue")\
            .select("*")\
            .is_("sent_at", "null")\
            .lte("send_at", datetime.now(timezone.utc).isoformat())\
            .execute()

        for nps in (pending.data or []):
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
                    supabase.table("nps_queue").update({
                        "sent_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", nps["id"]).execute()
                    print(f"✅ NPS отправлен в чат {chat_id}")

            except Exception as e:
                print(f"❌ Ошибка отправки NPS: {e}")

    except Exception as e:
        print(f"❌ Ошибка check_nps_queue_job: {e}")


async def generate_upsell_suggestion(deal: dict, chat_history: str) -> str:
    """Генерирует предложение допродажи через AI"""
    services = """
Наши услуги:
1. Геомаркетинг - продвижение на картах (Яндекс Карты, 2ГИС, Google Maps), работа с отзывами, заполнение карточек
2. Сайты - разработка и продвижение сайтов, SEO-оптимизация
3. Контекстная реклама - настройка и ведение рекламы в Яндекс Директ, Google Ads
4. Автоматизация - чат-боты, CRM-интеграции, автоматизация бизнес-процессов
"""

    prompt = f"""Ты - опытный аккаунт-менеджер. Проанализируй клиента и предложи допродажу.

{services}

Текущая услуга клиента: {deal.get('service_type', 'неизвестно')}
Название сделки: {deal.get('deal_name', 'неизвестно')}

История переписки с клиентом:
{chat_history}

Задача:
1. Определи какую дополнительную услугу можно предложить этому клиенту
2. Напиши короткое обоснование почему именно эта услуга подойдёт
3. Напиши готовое продающее сообщение для клиента (3-5 предложений, на "Вы", живой стиль)

Формат ответа:
УСЛУГА: [название услуги]
ПОЧЕМУ: [обоснование в 1-2 предложения]
СООБЩЕНИЕ:
[готовый текст для отправки клиенту]"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Ошибка генерации допродажи: {e}")
        return ""


async def monthly_upsell_job():
    """Ежемесячная задача допродажи - 1 числа"""
    try:
        print("💰 Запуск ежемесячной допродажи...")

        # Получаем все сделки из базы
        deals_resp = supabase.table("deals").select("*").execute()

        if not deals_resp.data:
            print("ℹ️ Нет активных сделок для допродажи")
            return

        for deal in deals_resp.data:
            try:
                chat_id = deal.get("chat_id")
                project_id = deal.get("project_id")

                if not chat_id or not project_id:
                    continue

                # Получаем историю чата
                chat_history = await get_chat_history_for_ai(chat_id, limit=20)

                # Генерируем предложение допродажи
                suggestion = await generate_upsell_suggestion(deal, chat_history)

                if suggestion:
                    # Отправляем проджекту в личку
                    message = (
                        f"💡 Предложение допродажи\n"
                        f"📋 Клиент: {deal.get('deal_name', 'Неизвестно')}\n\n"
                        f"{suggestion}"
                    )
                    await bot.send_message(int(project_id), message)
                    print(f"✅ Допродажа отправлена проджекту {project_id} для сделки {deal.get('deal_id')}")

            except Exception as e:
                print(f"❌ Ошибка допродажи для сделки {deal.get('deal_id')}: {e}")

    except Exception as e:
        print(f"❌ Ошибка monthly_upsell_job: {e}")


async def handle_nps(request: web.Request) -> web.Response:
    """Webhook для отправки NPS-опроса"""
    try:
        # Битрикс шлёт POST, но параметры в URL — читаем оба источника
        data = dict(request.query)  # GET параметры из URL
        if request.method == "POST":
            post_data = await request.post()
            data.update(dict(post_data))  # Добавляем POST данные

        # Логируем входящие данные
        print(f"📊 NPS webhook: query={dict(request.query)}, data={data}")
        with open("/var/log/nps.log", "a") as f:
            f.write(f"query={dict(request.query)}, data={data}\n")

        if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
            return web.json_response({"status": "error", "message": "Invalid secret"}, status=403)

        chat_id = data.get("chat_id")
        nps_type = data.get("type", "first")  # first, repeat, 3month
        topic_id = data.get("topic_id")

        if not chat_id:
            return web.json_response({"status": "error", "message": "Missing chat_id"}, status=400)

        # Очищаем chat_id
        chat_id = str(chat_id).strip()
        if " [" in chat_id:
            chat_id = chat_id.split(" [")[0].strip()

        thread_id = str(topic_id).strip() if topic_id else None

        # Ссылка на NPS
        nps_link = "https://vincora.ru/nps_first"

        # Разные сообщения в зависимости от типа
        if nps_type == "first":
            message = (
                "Мы завершили первый этап работы над вашим продвижением. "
                "Будем очень благодарны, если вы уделите минуту и оцените нашу работу - "
                f"это помогает нам становиться лучше.\n\n{nps_link}"
            )
        elif nps_type == "3month":
            message = (
                "Мы работаем вместе уже 3 месяца! Хотели бы узнать, как вам наше сотрудничество. "
                f"Пожалуйста, оцените нашу работу - это займёт всего минуту.\n\n{nps_link}"
            )
        else:
            message = (
                "Будем благодарны за обратную связь о нашей работе. "
                f"Пожалуйста, оцените нас - это займёт всего минуту.\n\n{nps_link}"
            )

        success = await send_to_chat(chat_id, message, thread_id)

        if success:
            print(f"📊 NPS ({nps_type}) отправлен в чат {chat_id}")
            return web.json_response({"status": "ok"})
        else:
            return web.json_response({"status": "error", "message": "Failed to send"}, status=500)

    except Exception as e:
        print(f"❌ Ошибка отправки NPS: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_document(request: web.Request) -> web.Response:
    """Обработка webhook для отправки документов (счета, акты)"""
    try:
        # Получаем параметры
        if request.method == "GET":
            data = dict(request.query)
        else:
            data = await request.post()
            data = dict(data)

        # Проверка секрета
        if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
            return web.json_response({"status": "error", "message": "Invalid secret"}, status=403)

        chat_id = data.get("chat_id")
        doc_type = data.get("type", "document")  # act, invoice, document
        doc_url = data.get("url", "")
        doc_pdf = data.get("pdf", "")
        topic_id = data.get("topic_id")

        # Логируем входящие данные
        with open("/var/log/documents.log", "a") as f:
            f.write(f"chat_id={chat_id}, type={doc_type}, pdf={doc_pdf}, url={doc_url}\n")
        print(f"📄 Document webhook: chat={chat_id}, type={doc_type}, pdf={doc_pdf}")

        if not chat_id:
            return web.json_response({"status": "error", "message": "Missing chat_id"}, status=400)

        # Очищаем chat_id от мусора Битрикса
        chat_id = str(chat_id).strip()
        if " [" in chat_id:
            chat_id = chat_id.split(" [")[0].strip()

        thread_id = str(topic_id).strip() if topic_id else None

        # Определяем тип документа для сообщения
        doc_names = {
            "act": "акт",
            "invoice": "счёт",
            "document": "документ"
        }
        doc_name = doc_names.get(doc_type, "документ")

        # Формируем сообщение
        file_link = doc_pdf or doc_url
        if file_link:
            message = f"Добрый день! Направляю вам {doc_name}: {file_link}"
        else:
            message = f"Добрый день! Ваш {doc_name} готов."

        # Отправляем
        success = await send_to_chat(chat_id, message, thread_id)

        if success:
            print(f"📄 Документ ({doc_type}) отправлен в чат {chat_id}")
            return web.json_response({"status": "ok"})
        else:
            return web.json_response({"status": "error", "message": "Failed to send"}, status=500)

    except Exception as e:
        print(f"❌ Ошибка отправки документа: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def start_webhook_server():
    """Запуск webhook-сервера"""
    app = web.Application()
    app.router.add_post("/bitrix/stage", handle_stage_change)
    app.router.add_get("/bitrix/stage", handle_stage_change)
    app.router.add_post("/bitrix/document", handle_document)
    app.router.add_get("/bitrix/document", handle_document)
    app.router.add_post("/bitrix/nps", handle_nps)
    app.router.add_get("/bitrix/nps", handle_nps)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    print(f"🌐 Webhook-сервер запущен на порту {WEBHOOK_PORT}")
    print(f"   /bitrix/stage — смена стадии")
    print(f"   /bitrix/document — счета и акты")
    print(f"   /bitrix/nps — NPS-опросы")


async def main():
    print("🤖 Бот-проджект запущен!")
    print(f"📋 Отслеживаю {len(PROJECT_IDS)} проджектов")
    print("⏳ Запущены напоминания: 15 / 30 / 60 минут от сообщения клиента")
    print("⏹️  Ctrl+C для остановки")

    # Запуск webhook-сервера для Битрикса
    await start_webhook_server()

    # Проверка неактивных чатов каждый день в 12:00
    scheduler.add_job(
        check_inactive_chats_job,
        "cron",
        hour=12,
        minute=0,
        id="inactive_chats_check",
        replace_existing=True
    )
    print("📢 Напоминание о неактивных чатах: 12:00 ежедневно (кроме выходных)")

    # Проверка праздников каждый день в 09:00
    scheduler.add_job(
        check_holiday_greetings_job,
        "cron",
        hour=9,
        minute=0,
        id="holiday_greetings_check",
        replace_existing=True
    )
    print("🎉 Проверка праздников: 09:00 ежедневно")

    # Проверка очереди NPS каждый час
    scheduler.add_job(
        check_nps_queue_job,
        "interval",
        hours=1,
        id="nps_queue_check",
        replace_existing=True
    )
    print("📊 Проверка NPS-очереди: каждый час")

    # Ежемесячная допродажа - 1 числа в 10:00
    scheduler.add_job(
        monthly_upsell_job,
        "cron",
        day=1,
        hour=10,
        minute=0,
        id="monthly_upsell",
        replace_existing=True
    )
    print("💰 Допродажа: 1 числа каждого месяца в 10:00")

    scheduler.start()
    print(f"🧭 Таймзона планировщика: {scheduler.timezone}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())