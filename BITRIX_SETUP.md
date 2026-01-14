# Настройка интеграции ProjectBot с Битрикс24

## Шаг 1: Подготовка сервера

### 1.1 Добавь переменные в .env

```bash
# Порт для webhook-сервера (по умолчанию 8080)
WEBHOOK_PORT=8080

# Секрет для проверки webhook (опционально, но рекомендуется)
WEBHOOK_SECRET=your_random_secret_string_here
```

### 1.2 Открой порт на сервере

Убедись, что порт 8080 (или какой указал) доступен извне.

Если используешь nginx, добавь проксирование:

```nginx
location /bitrix/ {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### 1.3 Установи aiohttp

```bash
pip install aiohttp
```

---

## Шаг 2: Создай таблицы в Supabase

1. Открой Supabase Dashboard → SQL Editor
2. Скопируй содержимое файла `supabase_setup.sql`
3. Выполни SQL

---

## Шаг 3: Настройка Битрикса

### 3.1 Узнай ID стадий в твоей воронке

1. Открой CRM → Сделки → Настройки воронки
2. В URL воронки будет ID (например `C5`)
3. Стадии имеют формат: `C5:NEW`, `C5:PREPARATION` и т.д.

**Или через API:**
```
https://your-domain.bitrix24.ru/rest/crm.status.list?ENTITY_ID=DEAL_STAGE
```

### 3.2 Настрой бизнес-процесс

1. CRM → Настройки → Автоматизация → Роботы и триггеры
2. Выбери нужную стадию
3. Добавь робота "Webhook"

**Настройки робота:**

- **URL:** `https://your-server.com/bitrix/stage`
- **Метод:** POST
- **Тип данных:** JSON

**Тело запроса:**
```json
{
    "deal_id": "{{ID}}",
    "stage_id": "GEO:AUDIT",
    "deal_name": "{{TITLE}}",
    "secret": "your_random_secret_string_here"
}
```

**ВАЖНО:** `stage_id` нужно прописать вручную для каждой стадии!
Используй те же ID, что добавил в таблицу `stage_actions`.

### 3.3 Пример для воронки геомаркетинга

Создай робота на каждую стадию:

| Стадия в Битрикс | stage_id для webhook |
|------------------|---------------------|
| Аудит | GEO:AUDIT |
| Стратегия | GEO:STRATEGY |
| Контент | GEO:CONTENT |
| Оптимизация | GEO:OPTIMIZATION |
| Отзывы | GEO:REVIEWS |
| Отчёт | GEO:REPORT |
| Завершено | GEO:DONE |

---

## Шаг 4: Привязка сделки к чату

### Вариант A: Через команду бота (рекомендую для начала)

1. Открой чат с клиентом в Telegram
2. Напиши команду:
```
/link 12345 geo
```
где `12345` — ID сделки в Битрикс

### Вариант B: Автоматически (будущее)

Можно добавить в Битрикс кастомное поле "Telegram Chat ID" и передавать его в webhook.

---

## Шаг 5: Тестирование

### 5.1 Проверь что webhook работает

```bash
curl -X POST https://your-server.com/health
# Должен вернуть: {"status": "ok", "service": "projectbot"}
```

### 5.2 Тестовый webhook

```bash
curl -X POST https://your-server.com/bitrix/stage \
  -H "Content-Type: application/json" \
  -d '{
    "deal_id": "12345",
    "stage_id": "GEO:AUDIT",
    "secret": "your_secret"
  }'
```

Если сделка `12345` привязана к чату — бот отправит сообщение.

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/link DEAL_ID SERVICE_TYPE` | Привязать сделку к текущему чату |
| `/deals` | Показать все привязанные сделки в чате |
| `/unlink DEAL_ID` | Отвязать сделку |
| `/who` | Показать ответственного проджекта |
| `/assign` | Назначить проджекта |

---

## Управление шаблонами

Шаблоны сообщений хранятся в таблице `stage_actions` в Supabase.

### Изменить текст сообщения:

```sql
UPDATE stage_actions
SET message_template = 'Новый текст сообщения'
WHERE stage_id = 'GEO:AUDIT' AND service_type = 'geo';
```

### Отключить автоотправку (только предложение проджекту):

```sql
UPDATE stage_actions
SET action_type = 'suggest_message'
WHERE stage_id = 'GEO:AUDIT' AND service_type = 'geo';
```

### Добавить новую стадию:

```sql
INSERT INTO stage_actions (stage_id, service_type, action_type, message_template)
VALUES ('GEO:NEW_STAGE', 'geo', 'send_message', 'Текст сообщения клиенту');
```

---

## Типы действий (action_type)

| Тип | Что делает |
|-----|-----------|
| `send_message` | Автоматически отправляет сообщение в чат клиенту |
| `suggest_message` | Отправляет предложение проджекту в ЛС |
| `schedule_nps` | Планирует отправку NPS-опроса через N дней |
| `notify_project` | Уведомляет проджекта о смене стадии |

---

## Плейсхолдеры в шаблонах

В `message_template` можно использовать:

- `{client_name}` — имя клиента
- `{deal_name}` — название сделки
- `{deal_id}` — ID сделки
- `{service_type}` — тип услуги

Пример:
```
Привет, {client_name}! По сделке "{deal_name}" начинаем работу.
```

---

## Troubleshooting

### Бот не отправляет сообщения

1. Проверь что сделка привязана: `/deals` в чате
2. Проверь что `stage_id` в webhook совпадает с `stage_actions`
3. Проверь логи бота на сервере

### Webhook возвращает 404

Сделка не найдена в таблице `deals`. Привяжи её командой `/link`.

### Webhook возвращает 403

Неверный `secret`. Проверь что в Битрикс и в `.env` одинаковый секрет.
