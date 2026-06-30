CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    gender CHAR(1) DEFAULT 'U',
    is_premium BOOLEAN DEFAULT FALSE,
    language_code VARCHAR(10),
    is_blocked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ad_links (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    code VARCHAR(50) UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS link_visits (
    id SERIAL PRIMARY KEY,
    link_id INTEGER REFERENCES ad_links(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    is_first_visit BOOLEAN DEFAULT TRUE,
    is_bot_suspected BOOLEAN DEFAULT FALSE,
    op_passed BOOLEAN DEFAULT FALSE,
    op_passed_at TIMESTAMPTZ,
    visited_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_link_visits_link_id ON link_visits(link_id);
CREATE INDEX IF NOT EXISTS idx_link_visits_user_id ON link_visits(user_id);
CREATE INDEX IF NOT EXISTS idx_link_visits_visited_at ON link_visits(visited_at);

CREATE TABLE IF NOT EXISTS op_channels (
    id SERIAL PRIMARY KEY,
    channel_id BIGINT UNIQUE,
    channel_username VARCHAR(255),
    title VARCHAR(255),
    invite_link VARCHAR(512),
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    channel_type VARCHAR(10) DEFAULT 'channel'
);
ALTER TABLE op_channels ALTER COLUMN channel_id DROP NOT NULL;
ALTER TABLE op_channels ADD COLUMN IF NOT EXISTS channel_type VARCHAR(10) DEFAULT 'channel';

-- Заявки на вступление в закрытые каналы. Для приватных каналов (режим «заявки»)
-- get_chat_member возвращает 'left', пока заявка не одобрена, поэтому членство так
-- не проверить. Вместо этого ловим chat_join_request и считаем ОП пройденной, если
-- юзер подал заявку. (Механизм адаптирован из KruzhokBot.)
CREATE TABLE IF NOT EXISTS join_requests (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_join_requests_user_chat ON join_requests(user_id, chat_id);

CREATE TABLE IF NOT EXISTS inline_uses (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    chat_id BIGINT,
    chat_type VARCHAR(20),
    query TEXT,
    gif_id VARCHAR(255),
    chosen_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inline_uses_chosen_at ON inline_uses(chosen_at);

CREATE TABLE IF NOT EXISTS gif_cache (
    gif_id VARCHAR(255) NOT NULL,
    text_hash VARCHAR(64) NOT NULL,
    telegram_file_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (gif_id, text_hash)
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id SERIAL PRIMARY KEY,
    content_type VARCHAR(50) NOT NULL,
    text TEXT,
    file_id VARCHAR(255),
    parse_mode VARCHAR(20) DEFAULT 'HTML',
    status VARCHAR(20) DEFAULT 'draft',
    total_users INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS templates (
    id SERIAL PRIMARY KEY,
    file_id VARCHAR(255) NOT NULL UNIQUE,
    file_unique_id VARCHAR(255),
    file_type VARCHAR(20) NOT NULL DEFAULT 'animation',
    title VARCHAR(255) NOT NULL DEFAULT '',
    use_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_templates_popular ON templates(use_count DESC) WHERE is_active = TRUE;

ALTER TABLE templates ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_templates_tags ON templates USING gin(tags);
ALTER TABLE templates ADD COLUMN IF NOT EXISTS submitted_by BIGINT;
ALTER TABLE templates ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT TRUE;
ALTER TABLE templates ADD COLUMN IF NOT EXISTS moderation_status VARCHAR(20) DEFAULT 'approved';
ALTER TABLE templates ADD COLUMN IF NOT EXISTS moderation_comment TEXT;

-- (welcome / main_menu seed moved below — the i18n migration recomposes the PK so
--  ON CONFLICT must reference (key, lang). See bottom of file.)

CREATE TABLE IF NOT EXISTS bot_messages (
    key VARCHAR(100) PRIMARY KEY,
    text TEXT NOT NULL DEFAULT '',
    entities JSONB DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS bot_buttons (
    key VARCHAR(100) PRIMARY KEY,
    rows JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS ad_placements (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ad_placements DROP COLUMN IF EXISTS format_type;
ALTER TABLE ad_placements DROP COLUMN IF EXISTS file_id;
ALTER TABLE ad_placements DROP COLUMN IF EXISTS file_type;
ALTER TABLE ad_placements ADD COLUMN IF NOT EXISTS caption_text TEXT;
ALTER TABLE ad_placements ADD COLUMN IF NOT EXISTS button_text VARCHAR(255);
ALTER TABLE ad_placements ADD COLUMN IF NOT EXISTS button_url VARCHAR(512);
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS buttons JSONB DEFAULT '[]';
ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_data JSONB DEFAULT NULL;
ALTER TABLE op_channels ADD COLUMN IF NOT EXISTS bot_token VARCHAR(100);

ALTER TABLE ad_placements ADD COLUMN IF NOT EXISTS limit_count INTEGER DEFAULT 3;
ALTER TABLE ad_placements ADD COLUMN IF NOT EXISTS limit_window_sec INTEGER DEFAULT 3600;

CREATE TABLE IF NOT EXISTS placement_sends (
    id BIGSERIAL PRIMARY KEY,
    placement_id INTEGER REFERENCES ad_placements(id) ON DELETE CASCADE,
    user_id BIGINT,
    chat_instance VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_placement_sends_pid_uid_created ON placement_sends(placement_id, user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_placement_sends_pid_chat ON placement_sends(placement_id, chat_instance) WHERE chat_instance IS NOT NULL;

CREATE TABLE IF NOT EXISTS bot_displays (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    caption_text TEXT,
    file_id VARCHAR(255),
    file_type VARCHAR(20),
    button_text VARCHAR(255),
    button_url VARCHAR(512),
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS display_views (
    user_id BIGINT NOT NULL,
    display_id INTEGER REFERENCES bot_displays(id) ON DELETE CASCADE,
    viewed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, display_id)
);

CREATE TABLE IF NOT EXISTS bot_admins (
    user_id BIGINT PRIMARY KEY,
    permissions TEXT[] NOT NULL DEFAULT '{}',
    granted_by BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_template_pins (
    user_id BIGINT NOT NULL,
    template_id INTEGER NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    pinned_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, template_id)
);
CREATE INDEX IF NOT EXISTS idx_user_template_pins_user ON user_template_pins(user_id, pinned_at DESC);

ALTER TABLE op_channels ADD COLUMN IF NOT EXISTS limit_visits INTEGER DEFAULT 0;

-- ===== Templates: separate uniqueness for public vs personal =====
-- Drop blanket UNIQUE on file_id (it blocks same file in both categories).
ALTER TABLE templates DROP CONSTRAINT IF EXISTS templates_file_id_key;
-- One public copy of a given file (stable Telegram identifier).
CREATE UNIQUE INDEX IF NOT EXISTS uniq_templates_public_file
    ON templates(file_unique_id)
    WHERE is_public = TRUE AND file_unique_id IS NOT NULL;
-- One personal copy per user.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_templates_personal_file
    ON templates(file_unique_id, submitted_by)
    WHERE is_public = FALSE AND file_unique_id IS NOT NULL;

-- ===== i18n =====
CREATE TABLE IF NOT EXISTS languages (
    code VARCHAR(5) PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    flag_emoji VARCHAR(10) NOT NULL DEFAULT '🌐',
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO languages (code, name, flag_emoji, sort_order)
VALUES ('ru', 'Русский', '🇷🇺', 0)
ON CONFLICT (code) DO NOTHING;

INSERT INTO languages (code, name, flag_emoji, sort_order)
VALUES ('en', 'English', '🇬🇧', 1)
ON CONFLICT (code) DO NOTHING;

ALTER TABLE users ADD COLUMN IF NOT EXISTS lang VARCHAR(5) DEFAULT 'ru';

-- bot_messages and bot_buttons: add lang column, repkey by (key, lang)
ALTER TABLE bot_messages ADD COLUMN IF NOT EXISTS lang VARCHAR(5) DEFAULT 'ru';
-- Опциональное медиа в менюшках (видео/гиф/фото). Если задано — экран показывается
-- как медиа-сообщение с подписью=text вместо текстового.
ALTER TABLE bot_messages ADD COLUMN IF NOT EXISTS file_id VARCHAR(255);
ALTER TABLE bot_messages ADD COLUMN IF NOT EXISTS file_type VARCHAR(20);
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'bot_messages_pkey' AND contype = 'p'
    ) THEN
        -- Check if PK is single-column (only `key`). If so, drop and re-add as composite.
        IF (
            SELECT count(*) FROM pg_attribute a
            JOIN pg_constraint c ON c.conrelid = a.attrelid
            WHERE c.conname = 'bot_messages_pkey' AND a.attnum = ANY(c.conkey)
        ) = 1 THEN
            ALTER TABLE bot_messages DROP CONSTRAINT bot_messages_pkey;
            ALTER TABLE bot_messages ADD PRIMARY KEY (key, lang);
        END IF;
    END IF;
END $$;

ALTER TABLE bot_buttons ADD COLUMN IF NOT EXISTS lang VARCHAR(5) DEFAULT 'ru';
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'bot_buttons_pkey' AND contype = 'p'
    ) THEN
        IF (
            SELECT count(*) FROM pg_attribute a
            JOIN pg_constraint c ON c.conrelid = a.attrelid
            WHERE c.conname = 'bot_buttons_pkey' AND a.attnum = ANY(c.conkey)
        ) = 1 THEN
            ALTER TABLE bot_buttons DROP CONSTRAINT bot_buttons_pkey;
            ALTER TABLE bot_buttons ADD PRIMARY KEY (key, lang);
        END IF;
    END IF;
END $$;

-- i18n strings: hardcoded UI strings keyed by (key, lang)
CREATE TABLE IF NOT EXISTS i18n_strings (
    key VARCHAR(150) NOT NULL,
    lang VARCHAR(5) NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (key, lang)
);

-- Seed default RU welcome + main_menu (idempotent on composite PK)
INSERT INTO bot_messages (key, lang, text, entities)
VALUES ('welcome', 'ru',
  E'🌐 Привет! Я бот, с помощью которого можно накладывать текст на мемные шаблоны.\n\nЧтобы разместить текст в нижней части шаблона, разделите фразы точкой.\nПодробная видеоинструкция по работе @гифыч прикреплена к этому сообщению.',
  '[]')
ON CONFLICT (key, lang) DO NOTHING;

INSERT INTO bot_buttons (key, lang, rows)
VALUES ('main_menu', 'ru', '[
  [{"text":"🖼 Добавить шаблон","callback_data":"template:add_user"}],
  [{"text":"💎 Гифыч Premium","callback_data":"premium:open"}],
  [{"text":"📰 Новости ↗","url":"https://t.me/your_news_channel"},{"text":"📁 Шаблоны ↗","url":"https://t.me/your_media_channel"}],
  [{"text":"⚙️ Настройки","callback_data":"settings:open"}]
]')
ON CONFLICT (key, lang) DO NOTHING;
