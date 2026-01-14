-- ============================================
-- SQL для создания таблиц в Supabase
-- ProjectBot: Интеграция с Битрикс24
-- ============================================

-- 1. Таблица deals — связка сделка ↔ чат
CREATE TABLE IF NOT EXISTS deals (
    id SERIAL PRIMARY KEY,
    deal_id TEXT UNIQUE NOT NULL,          -- ID сделки в Битрикс
    deal_name TEXT,                        -- Название сделки/чата
    chat_id TEXT NOT NULL,                 -- ID чата в Telegram
    thread_id TEXT,                        -- ID топика (NULL если обычный чат)
    client_name TEXT,                      -- Имя клиента
    service_type TEXT,                     -- geo, context, site, serm
    current_stage_id TEXT,                 -- Текущая стадия
    project_id BIGINT,                     -- ID проджекта в Telegram
    is_prolongation BOOLEAN DEFAULT FALSE, -- Услуга на пролонгации?
    bitrix_data JSONB,                     -- Доп. данные из Битрикс
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для deals
CREATE INDEX IF NOT EXISTS idx_deals_chat_id ON deals(chat_id);
CREATE INDEX IF NOT EXISTS idx_deals_service_type ON deals(service_type);
CREATE INDEX IF NOT EXISTS idx_deals_project_id ON deals(project_id);


-- 2. Таблица stages — справочник стадий (опционально, для читаемости)
CREATE TABLE IF NOT EXISTS stages (
    id SERIAL PRIMARY KEY,
    stage_id TEXT NOT NULL,                -- ID из Битрикс (например "GEO:AUDIT")
    service_type TEXT NOT NULL,            -- geo, context, site, serm
    stage_name TEXT NOT NULL,              -- Человекочитаемое название
    stage_order INT DEFAULT 0,             -- Порядок в воронке
    UNIQUE(stage_id, service_type)
);


-- 3. Таблица stage_actions — что делать при переходе на стадию
CREATE TABLE IF NOT EXISTS stage_actions (
    id SERIAL PRIMARY KEY,
    stage_id TEXT NOT NULL,                -- ID стадии
    service_type TEXT NOT NULL,            -- geo, context, site, serm

    -- Тип действия
    action_type TEXT NOT NULL,             -- send_message, suggest_message, schedule_nps, notify_project

    -- Для send_message / suggest_message
    message_template TEXT,                 -- Шаблон с плейсхолдерами {client_name}, {deal_name}
    use_ai BOOLEAN DEFAULT FALSE,          -- Генерировать через GPT?
    ai_prompt TEXT,                        -- Промпт для GPT если use_ai=true

    -- Для schedule_nps
    nps_delay_days INT,                    -- Через сколько дней отправить NPS
    nps_link TEXT,                         -- Ссылка на опрос

    -- Настройки
    is_active BOOLEAN DEFAULT TRUE,
    priority INT DEFAULT 0,                -- Порядок выполнения (меньше = раньше)

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для stage_actions
CREATE INDEX IF NOT EXISTS idx_stage_actions_lookup
    ON stage_actions(stage_id, service_type, is_active);


-- 4. Таблица nps_queue — очередь NPS-опросов
CREATE TABLE IF NOT EXISTS nps_queue (
    id SERIAL PRIMARY KEY,
    deal_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    thread_id TEXT,
    service_type TEXT,
    trigger_type TEXT,                    -- 'completion', 'prolongation_3m'
    send_at TIMESTAMPTZ NOT NULL,         -- Когда отправить
    sent_at TIMESTAMPTZ,                  -- Когда отправили (NULL = не отправлено)
    nps_link TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индекс для поиска неотправленных
CREATE INDEX IF NOT EXISTS idx_nps_queue_pending
    ON nps_queue(send_at) WHERE sent_at IS NULL;


-- ============================================
-- ПРИМЕРЫ ДАННЫХ ДЛЯ ГЕОМАРКЕТИНГА
-- ============================================

-- Стадии геомаркетинга (для справки)
INSERT INTO stages (stage_id, service_type, stage_name, stage_order) VALUES
('GEO:NEW', 'geo', 'Новая заявка', 1),
('GEO:AUDIT', 'geo', 'Аудит карточек', 2),
('GEO:STRATEGY', 'geo', 'Разработка стратегии', 3),
('GEO:CONTENT', 'geo', 'Подготовка контента', 4),
('GEO:OPTIMIZATION', 'geo', 'Оптимизация карточек', 5),
('GEO:REVIEWS', 'geo', 'Работа с отзывами', 6),
('GEO:MONITORING', 'geo', 'Мониторинг', 7),
('GEO:REPORT', 'geo', 'Отчёт', 8),
('GEO:DONE', 'geo', 'Завершено', 9)
ON CONFLICT (stage_id, service_type) DO NOTHING;


-- Действия на стадиях геомаркетинга
-- ВАЖНО: stage_id должны совпадать с тем, что приходит из Битрикса!

INSERT INTO stage_actions (stage_id, service_type, action_type, message_template, priority) VALUES
('GEO:AUDIT', 'geo', 'send_message',
 'Привет! 👋

Начинаем аудит ваших карточек на картах. Проверим Яндекс Карты, Google Maps и 2ГИС.

Результаты пришлём в течение 2-3 рабочих дней 📊', 0),

('GEO:STRATEGY', 'geo', 'send_message',
 'Аудит готов ✅

Подготовили стратегию оптимизации — сейчас пришлю документ с рекомендациями.

Посмотрите и напишите, если будут вопросы!', 0),

('GEO:CONTENT', 'geo', 'send_message',
 'Приступаем к подготовке контента 📝

Нам понадобятся фото вашего бизнеса (интерьер, экстерьер, команда, процесс работы).

Есть ли у вас готовые фото? Или нужна помощь с фотосессией?', 0),

('GEO:OPTIMIZATION', 'geo', 'send_message',
 'Контент готов, приступаем к оптимизации карточек 🔧

Будем обновлять информацию, добавлять фото и работать над описаниями.

Процесс займёт 3-5 рабочих дней.', 0),

('GEO:REVIEWS', 'geo', 'suggest_message',  -- Предлагаем проджекту, не отправляем автоматом
 'Карточки оптимизированы ✅

Теперь фокусируемся на отзывах. Подскажите, есть ли база клиентов, которых можно попросить оставить отзыв?

Это значительно ускорит рост рейтинга 🌟', 0),

('GEO:REPORT', 'geo', 'send_message',
 'Подготовили отчёт за период 📈

Сейчас пришлю документ с результатами:
• Динамика позиций
• Статистика просмотров
• Рекомендации на следующий месяц', 0),

('GEO:DONE', 'geo', 'schedule_nps', NULL, 0);

-- Обновляем настройки NPS для стадии DONE
UPDATE stage_actions
SET nps_delay_days = 3,
    nps_link = 'https://forms.yandex.ru/u/YOUR_FORM_ID/'
WHERE stage_id = 'GEO:DONE'
  AND service_type = 'geo'
  AND action_type = 'schedule_nps';


-- ============================================
-- 5. Таблица client_knowledge — база знаний по клиентам
-- ============================================

CREATE TABLE IF NOT EXISTS client_knowledge (
    id SERIAL PRIMARY KEY,
    chat_id TEXT UNIQUE NOT NULL,           -- ID чата в Telegram (уникальный)
    client_name TEXT,                       -- Название компании/клиента
    decision_maker TEXT,                    -- ЛПР (имя, должность)
    contact_person TEXT,                    -- Контактное лицо
    preferences TEXT,                       -- Что любит
    dislikes TEXT,                          -- Что не любит
    communication_style TEXT,               -- Стиль общения (формальный/дружеский)
    timezone TEXT DEFAULT 'Europe/Moscow',  -- Часовой пояс
    best_contact_time TEXT,                 -- Лучшее время для связи
    service_type TEXT,                      -- Услуга (geo, context, site...)
    start_date DATE,                        -- Дата начала работы
    payment_day INT,                        -- День оплаты (1-31)
    notes TEXT,                             -- Свободные заметки
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индекс для client_knowledge
CREATE INDEX IF NOT EXISTS idx_client_knowledge_chat_id ON client_knowledge(chat_id);


-- ============================================
-- 6. Таблица reminders — напоминания о договорённостях
-- ============================================

CREATE TABLE IF NOT EXISTS reminders (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,                  -- ID чата с клиентом
    chat_name TEXT,                         -- Название чата
    project_id BIGINT NOT NULL,             -- ID проджекта (кому напоминать)
    reminder_text TEXT NOT NULL,            -- Текст напоминания
    context TEXT,                           -- Контекст (из какого сообщения)
    remind_at TIMESTAMPTZ NOT NULL,         -- Когда напомнить
    status TEXT DEFAULT 'pending',          -- pending, sent, cancelled
    sent_at TIMESTAMPTZ,                    -- Когда отправили
    source_message_id BIGINT,               -- ID исходного сообщения
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для reminders
CREATE INDEX IF NOT EXISTS idx_reminders_pending
    ON reminders(remind_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_reminders_project
    ON reminders(project_id, status);


-- ============================================
-- ПОЛЕЗНЫЕ ЗАПРОСЫ
-- ============================================

-- Посмотреть все действия для сервиса:
-- SELECT * FROM stage_actions WHERE service_type = 'geo' ORDER BY stage_id, priority;

-- Посмотреть все привязанные сделки:
-- SELECT * FROM deals ORDER BY created_at DESC;

-- Посмотреть очередь NPS:
-- SELECT * FROM nps_queue WHERE sent_at IS NULL ORDER BY send_at;

-- Обновить шаблон сообщения:
-- UPDATE stage_actions SET message_template = 'Новый текст' WHERE stage_id = 'GEO:AUDIT' AND service_type = 'geo';

-- Отключить действие:
-- UPDATE stage_actions SET is_active = false WHERE id = 1;
