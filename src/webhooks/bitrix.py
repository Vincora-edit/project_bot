"""
Webhook-обработчики для интеграции с Битрикс24.

Эндпоинты:
- /bitrix/stage — смена стадии сделки
- /bitrix/nps — отправка NPS-опроса
- /bitrix/document — отправка документов (акты, счета)
- /health — проверка здоровья сервиса
"""

from datetime import datetime, timezone, timedelta

from aiohttp import web

from src.config import settings
from src.core import db, bot
from src.services.openai_service import ai_service
from src.utils.logging import get_logger


logger = get_logger(__name__)


async def send_to_chat(chat_id: str, message: str, thread_id: str | None = None) -> bool:
    """Отправка сообщения в чат с учётом топика."""
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
        logger.error(f"Ошибка отправки в чат {chat_id}: {e}")
        return False


async def send_document_to_chat(
    chat_id: str,
    document_url: str,
    caption: str,
    thread_id: str | None = None
) -> bool:
    """Отправка документа (PDF) в чат."""
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
        logger.error(f"Ошибка отправки документа в чат {chat_id}: {e}")
        return False


def clean_chat_id(chat_id: str) -> str:
    """Очищает chat_id от мусора Битрикса."""
    chat_id = str(chat_id).strip()
    if " [" in chat_id:
        chat_id = chat_id.split(" [")[0].strip()
    return chat_id


async def get_chat_history_for_ai(chat_id: str, limit: int = 15) -> str:
    """Получает историю чата для контекста AI."""
    try:
        messages = db.get_recent_messages(chat_id, 999999999, limit)

        if not messages:
            return "История чата пуста."

        history_lines = []
        for msg in messages:
            role = "Проджект" if msg.get("is_project") else "Клиент"
            name = msg.get("from_name", "Unknown")
            text = msg.get("text", "")[:200]
            if text:
                history_lines.append(f"{role} ({name}): {text}")

        return "\n".join(history_lines) if history_lines else "История чата пуста."

    except Exception as e:
        logger.error(f"Ошибка получения истории чата: {e}")
        return "Не удалось загрузить историю."


def format_stage_message(template: str, deal: dict) -> str:
    """Подставляет плейсхолдеры в шаблон сообщения."""
    if not template:
        return ""

    return template.format(
        client_name=deal.get("client_name", ""),
        deal_name=deal.get("deal_name", ""),
        service_type=deal.get("service_type", ""),
        deal_id=deal.get("deal_id", "")
    )


async def execute_stage_action(
    action: dict,
    deal: dict,
    chat_id: str,
    thread_id: str | None
) -> bool:
    """Выполняет конкретное действие при смене стадии."""
    action_type = action.get("action_type")
    template = action.get("message_template", "")

    try:
        if action_type == "send_message":
            if action.get("use_ai"):
                ai_prompt = action.get("ai_prompt") or "Напиши сообщение клиенту о переходе на новый этап работы."
                chat_history = await get_chat_history_for_ai(chat_id)
                message = await ai_service.generate_stage_message(ai_prompt, deal, chat_history, template)
            else:
                message = format_stage_message(template, deal)

            if not message:
                logger.warning(f"Пустое сообщение для стадии {action.get('stage_id')}")
                return False

            return await send_to_chat(chat_id, message, thread_id)

        elif action_type == "suggest_message":
            if action.get("use_ai"):
                ai_prompt = action.get("ai_prompt") or "Предложи сообщение для клиента."
                chat_history = await get_chat_history_for_ai(chat_id)
                message = await ai_service.generate_stage_message(ai_prompt, deal, chat_history, template)
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
            delay_days = action.get("nps_delay_days", 3)
            send_at = datetime.now(timezone.utc) + timedelta(days=delay_days)

            db.add_to_nps_queue({
                "deal_id": deal.get("deal_id"),
                "chat_id": chat_id,
                "thread_id": thread_id,
                "service_type": deal.get("service_type"),
                "send_at": send_at.isoformat(),
                "nps_link": action.get("nps_link", "")
            })

            logger.info(f"NPS запланирован на {send_at.isoformat()} для сделки {deal.get('deal_id')}")
            return True

        elif action_type == "send_nps":
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
                chat_history = await get_chat_history_for_ai(chat_id)
                message = await ai_service.generate_stage_message(ai_prompt, deal, chat_history, template)
                if nps_link not in message:
                    message += f"\n\n{nps_link}"
            else:
                message = template or f"Будем благодарны за оценку нашей работы: {nps_link}"

            return await send_to_chat(chat_id, message, thread_id)

        elif action_type == "notify_project":
            project_id = deal.get("project_id")
            if project_id:
                await bot.send_message(
                    int(project_id),
                    f"📌 {deal.get('deal_name', 'Сделка')}: перешла на стадию {action.get('stage_id')}"
                )
                return True
            return False

        else:
            logger.warning(f"Неизвестный тип действия: {action_type}")
            return False

    except Exception as e:
        logger.error(f"Ошибка выполнения действия {action_type}: {e}")
        return False


async def handle_stage_change(request: web.Request) -> web.Response:
    """
    Webhook от Битрикса при смене стадии сделки.

    Параметры (GET или POST):
    - chat_id: ID чата Telegram (обязательно)
    - stage_id: ID стадии в Битриксе (обязательно)
    - deal_id: ID сделки (опционально)
    - title: Название сделки (опционально)
    - service: Тип услуги (опционально, по умолчанию geo)
    - topic_id: ID топика (опционально)
    - secret: Секретный ключ (опционально)
    - pdf: Ссылка на документ (опционально)
    """
    try:
        data = {}

        if request.query:
            data = dict(request.query)
            logger.info(f"Webhook GET: {data}")
        elif request.content_type == "application/json":
            data = await request.json()
            logger.info(f"Webhook JSON: {data}")
        elif request.content_type == "application/x-www-form-urlencoded":
            post_data = await request.post()
            data = dict(post_data)
            logger.info(f"Webhook FORM: {data}")
        else:
            try:
                data = await request.json()
                logger.info(f"Webhook JSON (fallback): {data}")
            except:
                logger.warning("Не удалось распарсить данные webhook")
                return web.json_response(
                    {"status": "error", "message": "Invalid request format"},
                    status=400
                )

        # Проверка секрета
        if settings.webhook_secret and data.get("secret") != settings.webhook_secret:
            logger.warning("Неверный secret в webhook")
            return web.json_response(
                {"status": "error", "message": "Invalid secret"},
                status=403
            )

        chat_id = data.get("chat_id")
        stage_id = data.get("stage_id")
        deal_id = data.get("deal_id", "unknown")
        title = data.get("title", "")
        service_type = data.get("service", "geo")
        topic_id = data.get("topic_id")
        doc_pdf = data.get("pdf", "")

        if not chat_id:
            return web.json_response(
                {"status": "error", "message": "Missing chat_id"},
                status=400
            )

        if not stage_id:
            return web.json_response(
                {"status": "error", "message": "Missing stage_id"},
                status=400
            )

        chat_id = clean_chat_id(chat_id)
        thread_id = str(topic_id).strip() if topic_id else None

        logger.info(f"Обработка: deal={deal_id}, stage={stage_id}, chat={chat_id}, pdf={doc_pdf}")

        # Если передан pdf — отправка документа
        if doc_pdf:
            doc_type = data.get("doc_type", "act")

            if doc_type == "invoice":
                caption = "Добрый день! Направляю вам счёт на оплату. Если возникнут вопросы — пишите, всё обсудим."
            else:
                caption = "Добрый день! Работы завершены, направляю вам закрывающий акт. Спасибо за сотрудничество, было приятно работать вместе!"

            if doc_pdf.endswith('.pdf') or '/disk/' in doc_pdf:
                success = await send_document_to_chat(chat_id, doc_pdf, caption, thread_id)
                if success:
                    logger.info(f"Документ отправлен как файл в чат {chat_id}")
                    return web.json_response({"status": "ok", "action": "document_sent"})

            message = f"{caption}\n\nСкачать документ: {doc_pdf}"
            success = await send_to_chat(chat_id, message, thread_id)
            if success:
                logger.info(f"Ссылка на документ отправлена в чат {chat_id}")
                return web.json_response({"status": "ok", "action": "document_link_sent"})
            else:
                return web.json_response(
                    {"status": "error", "message": "Failed to send document"},
                    status=500
                )

        # Находим действия для стадии
        actions = db.get_stage_actions(stage_id, service_type)

        if not actions:
            logger.info(f"Нет действий для стадии {stage_id} / {service_type} - создаю автоматически")

            stage_name = stage_id.split(" / ")[-1] if " / " in stage_id else stage_id

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
            db.create_stage_action(new_action)
            logger.info(f"Создана новая запись для стадии: {stage_id}")

            actions = [new_action]

        deal = {
            "deal_id": deal_id,
            "deal_name": title,
            "service_type": service_type,
            "chat_id": chat_id,
            "thread_id": thread_id
        }

        actions_executed = 0
        for action in actions:
            success = await execute_stage_action(action, deal, chat_id, thread_id)
            if success:
                actions_executed += 1

        logger.info(f"Обработано {actions_executed} действий для сделки {deal_id}")
        return web.json_response({"status": "ok", "actions": actions_executed})

    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_nps(request: web.Request) -> web.Response:
    """Webhook для отправки NPS-опроса."""
    try:
        # Битрикс шлёт POST, но параметры в URL — читаем оба источника
        data = dict(request.query)
        if request.method == "POST":
            post_data = await request.post()
            data.update(dict(post_data))

        logger.info(f"NPS webhook: {data}")

        if settings.webhook_secret and data.get("secret") != settings.webhook_secret:
            return web.json_response(
                {"status": "error", "message": "Invalid secret"},
                status=403
            )

        chat_id = data.get("chat_id")
        nps_type = data.get("type", "first")
        topic_id = data.get("topic_id")

        if not chat_id:
            return web.json_response(
                {"status": "error", "message": "Missing chat_id"},
                status=400
            )

        chat_id = clean_chat_id(chat_id)
        thread_id = str(topic_id).strip() if topic_id else None

        nps_link = "https://vincora.ru/nps_first"

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
            logger.info(f"NPS ({nps_type}) отправлен в чат {chat_id}")
            return web.json_response({"status": "ok"})
        else:
            return web.json_response(
                {"status": "error", "message": "Failed to send"},
                status=500
            )

    except Exception as e:
        logger.error(f"Ошибка отправки NPS: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_document(request: web.Request) -> web.Response:
    """Обработка webhook для отправки документов (счета, акты)."""
    try:
        if request.method == "GET":
            data = dict(request.query)
        else:
            data = await request.post()
            data = dict(data)

        if settings.webhook_secret and data.get("secret") != settings.webhook_secret:
            return web.json_response(
                {"status": "error", "message": "Invalid secret"},
                status=403
            )

        chat_id = data.get("chat_id")
        doc_type = data.get("type", "document")
        doc_url = data.get("url", "")
        doc_pdf = data.get("pdf", "")
        topic_id = data.get("topic_id")

        logger.info(f"Document webhook: chat={chat_id}, type={doc_type}, pdf={doc_pdf}")

        if not chat_id:
            return web.json_response(
                {"status": "error", "message": "Missing chat_id"},
                status=400
            )

        chat_id = clean_chat_id(chat_id)
        thread_id = str(topic_id).strip() if topic_id else None

        doc_names = {
            "act": "акт",
            "invoice": "счёт",
            "document": "документ"
        }
        doc_name = doc_names.get(doc_type, "документ")

        file_link = doc_pdf or doc_url
        if file_link:
            message = f"Добрый день! Направляю вам {doc_name}: {file_link}"
        else:
            message = f"Добрый день! Ваш {doc_name} готов."

        success = await send_to_chat(chat_id, message, thread_id)

        if success:
            logger.info(f"Документ ({doc_type}) отправлен в чат {chat_id}")
            return web.json_response({"status": "ok"})
        else:
            return web.json_response(
                {"status": "error", "message": "Failed to send"},
                status=500
            )

    except Exception as e:
        logger.error(f"Ошибка отправки документа: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    """Health check эндпоинт."""
    return web.json_response({"status": "ok", "service": "projectbot"})


def create_webhook_app() -> web.Application:
    """Создаёт aiohttp приложение с webhook-роутами."""
    app = web.Application()
    app.router.add_post("/bitrix/stage", handle_stage_change)
    app.router.add_get("/bitrix/stage", handle_stage_change)
    app.router.add_post("/bitrix/document", handle_document)
    app.router.add_get("/bitrix/document", handle_document)
    app.router.add_post("/bitrix/nps", handle_nps)
    app.router.add_get("/bitrix/nps", handle_nps)
    app.router.add_get("/health", handle_health)
    return app
