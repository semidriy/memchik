from datetime import date, timedelta, datetime, timezone
from asyncpg import Pool, Connection
from aiogram.types import User as TgUser


async def upsert_user(pool: Pool, tg_user: TgUser, gender: str = "U"):
    # Язык Telegram-клиента нормализуем к 2-буквенному коду (en-US -> en) и кладём в
    # users.lang ТОЛЬКО при первой вставке. Так новый юзер автоматически получает
    # язык бота, если он добавлен и активен (get_user_lang сам проверит активность и
    # откатится на ru, если язык не подключён), но ручной выбор языка не перетирается.
    tg_lang = (tg_user.language_code or "").split("-")[0].lower() or None
    await pool.execute(
        """
        INSERT INTO users (id, username, first_name, last_name, gender, is_premium, language_code, lang)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            is_premium = EXCLUDED.is_premium,
            last_active = NOW()
        """,
        tg_user.id,
        tg_user.username,
        tg_user.first_name,
        tg_user.last_name,
        gender,
        tg_user.is_premium or False,
        tg_user.language_code,
        tg_lang,
    )


async def get_user(pool: Pool, user_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return dict(row) if row else None


async def get_user_by_username(pool: Pool, username: str) -> dict | None:
    """Для «подарить премку»: получатель ищется среди тех, кто запускал бота."""
    row = await pool.fetchrow(
        "SELECT * FROM users WHERE lower(username) = lower($1) ORDER BY last_active DESC LIMIT 1",
        username.lstrip("@"),
    )
    return dict(row) if row else None


async def set_user_bot_premium(pool: Pool, user_id: int, until: datetime) -> None:
    await pool.execute(
        "UPDATE users SET premium_until = $1 WHERE id = $2",
        until, user_id,
    )


async def is_user_bot_premium(pool: Pool, user_id: int) -> bool:
    row = await pool.fetchrow(
        "SELECT premium_until FROM users WHERE id = $1", user_id
    )
    if not row or not row["premium_until"]:
        return False
    return row["premium_until"] > datetime.now(timezone.utc)


async def get_users_count(pool: Pool) -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE last_active::date = $1) AS active_today,
            COUNT(*) FILTER (WHERE last_active::date = $2) AS active_yesterday,
            COUNT(*) FILTER (WHERE created_at::date = $1) AS new_today,
            COUNT(*) FILTER (WHERE last_active >= NOW() - INTERVAL '1 hour') AS active_hour,
            COUNT(*) FILTER (WHERE last_active >= NOW() - INTERVAL '24 hours') AS active_24h,
            COUNT(*) FILTER (WHERE gender = 'M') AS male,
            COUNT(*) FILTER (WHERE gender = 'F') AS female,
            COUNT(*) FILTER (WHERE is_blocked = TRUE) AS blocked
        FROM users
        """,
        today,
        yesterday,
    )
    return dict(row)


async def get_users_export(pool: Pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, username, first_name, last_name, gender, is_premium,
               language_code, is_blocked, created_at, last_active
        FROM users ORDER BY created_at DESC
        """
    )
    return [dict(r) for r in rows]


async def get_all_user_ids(pool: Pool) -> list[int]:
    rows = await pool.fetch("SELECT id FROM users WHERE is_blocked = FALSE")
    return [r["id"] for r in rows]


async def mark_user_blocked(pool: Pool, user_id: int):
    await pool.execute("UPDATE users SET is_blocked = TRUE WHERE id = $1", user_id)


# --- Ad links ---

async def create_ad_link(pool: Pool, name: str, code: str) -> dict:
    row = await pool.fetchrow(
        "INSERT INTO ad_links (name, code) VALUES ($1, $2) RETURNING *",
        name, code,
    )
    return dict(row)


async def get_ad_link_by_code(pool: Pool, code: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM ad_links WHERE code = $1 AND is_active = TRUE", code)
    return dict(row) if row else None


async def get_all_ad_links(pool: Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM ad_links ORDER BY created_at DESC")
    return [dict(r) for r in rows]


async def toggle_ad_link(pool: Pool, link_id: int, is_active: bool):
    await pool.execute("UPDATE ad_links SET is_active = $1 WHERE id = $2", is_active, link_id)


async def delete_ad_link(pool: Pool, link_id: int):
    await pool.execute("DELETE FROM ad_links WHERE id = $1", link_id)


async def record_visit(pool: Pool, link_id: int, user_id: int, is_bot_suspected: bool) -> bool:
    existing = await pool.fetchrow(
        "SELECT id FROM link_visits WHERE link_id = $1 AND user_id = $2",
        link_id, user_id,
    )
    is_first = existing is None
    await pool.execute(
        """
        INSERT INTO link_visits (link_id, user_id, is_first_visit, is_bot_suspected)
        VALUES ($1, $2, $3, $4)
        """,
        link_id, user_id, is_first, is_bot_suspected,
    )
    return is_first


async def mark_op_passed(pool: Pool, link_id: int, user_id: int):
    await pool.execute(
        """
        UPDATE link_visits SET op_passed = TRUE, op_passed_at = NOW()
        WHERE link_id = $1 AND user_id = $2 AND op_passed = FALSE
        """,
        link_id, user_id,
    )


async def mark_op_passed_by_code(pool: Pool, link_code: str, user_id: int):
    await pool.execute(
        """
        UPDATE link_visits SET op_passed = TRUE, op_passed_at = NOW()
        WHERE link_id = (SELECT id FROM ad_links WHERE code = $1)
          AND user_id = $2 AND op_passed = FALSE
        """,
        link_code, user_id,
    )


async def get_link_stats(pool: Pool, link_id: int) -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_clicks,
            COUNT(*) FILTER (WHERE is_first_visit = TRUE) AS unique_clicks,
            COUNT(*) FILTER (WHERE is_bot_suspected = TRUE) AS bot_clicks,
            COUNT(*) FILTER (WHERE is_bot_suspected = FALSE) AS live_clicks,
            COUNT(*) FILTER (WHERE op_passed = TRUE) AS op_passed,
            COUNT(*) FILTER (WHERE visited_at::date = $2) AS today_clicks,
            COUNT(*) FILTER (WHERE visited_at::date = $3) AS yesterday_clicks,
            COUNT(*) FILTER (WHERE is_first_visit AND is_bot_suspected = FALSE) AS unique_live,
            COUNT(*) FILTER (WHERE is_first_visit AND is_bot_suspected = FALSE
                AND u.gender = 'M') AS male_unique,
            COUNT(*) FILTER (WHERE is_first_visit AND is_bot_suspected = FALSE
                AND u.gender = 'F') AS female_unique
        FROM link_visits lv
        LEFT JOIN users u ON u.id = lv.user_id
        WHERE lv.link_id = $1
        """,
        link_id, today, yesterday,
    )
    return dict(row)


# --- OP channels ---

async def get_active_op_channels(pool: Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM op_channels WHERE is_active = TRUE ORDER BY sort_order"
    )
    return [dict(r) for r in rows]


async def get_all_op_channels(pool: Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM op_channels ORDER BY sort_order")
    return [dict(r) for r in rows]


# --- Join requests (закрытые каналы / режим «заявки») ---

async def log_join_request(pool: Pool, user_id: int, chat_id: int) -> None:
    """Запоминаем заявку на вступление в закрытый канал. Используется для ОП:
    в приватном канале нельзя проверить членство через get_chat_member, пока заявка
    висит, поэтому факт заявки = подписка выполнена."""
    await pool.execute(
        """
        INSERT INTO join_requests (user_id, chat_id) VALUES ($1, $2)
        ON CONFLICT (user_id, chat_id) DO NOTHING
        """,
        user_id, chat_id,
    )


async def check_join_request(pool: Pool, user_id: int, chat_id: int) -> bool:
    return bool(await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM join_requests WHERE user_id = $1 AND chat_id = $2)",
        user_id, chat_id,
    ))


async def add_op_channel(
    pool: Pool, channel_id: int | None, username: str, title: str,
    invite_link: str, channel_type: str = "channel", bot_token: str | None = None,
    limit_visits: int = 0,
) -> dict:
    if channel_id is not None:
        row = await pool.fetchrow(
            """
            INSERT INTO op_channels
                (channel_id, channel_username, title, invite_link, channel_type, bot_token, limit_visits)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (channel_id) DO UPDATE SET
                channel_username = EXCLUDED.channel_username,
                title = EXCLUDED.title,
                invite_link = EXCLUDED.invite_link,
                channel_type = EXCLUDED.channel_type,
                bot_token = EXCLUDED.bot_token,
                limit_visits = EXCLUDED.limit_visits,
                is_active = TRUE
            RETURNING *
            """,
            channel_id, username, title, invite_link, channel_type, bot_token, limit_visits,
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO op_channels
                (channel_id, channel_username, title, invite_link, channel_type, bot_token, limit_visits)
            VALUES (NULL, $1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            username, title, invite_link, channel_type, bot_token, limit_visits,
        )
    return dict(row)


async def toggle_op_channel(pool: Pool, channel_id_db: int, is_active: bool):
    await pool.execute(
        "UPDATE op_channels SET is_active = $1 WHERE id = $2", is_active, channel_id_db
    )


async def delete_op_channel(pool: Pool, channel_id_db: int):
    await pool.execute("DELETE FROM op_channels WHERE id = $1", channel_id_db)


# --- Inline uses ---

async def record_inline_use(pool: Pool, user_id: int, chat_id: int, chat_type: str, query: str, gif_id: str):
    await pool.execute(
        "INSERT INTO inline_uses (user_id, chat_id, chat_type, query, gif_id) VALUES ($1, $2, $3, $4, $5)",
        user_id, chat_id, chat_type, query, gif_id,
    )


async def get_inline_stats(pool: Pool) -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT user_id) AS unique_users,
            COUNT(*) FILTER (WHERE chosen_at::date = $1) AS today_uses,
            COUNT(*) FILTER (WHERE chosen_at::date = $2) AS yesterday_uses,
            COUNT(DISTINCT chat_id) AS unique_chats,
            COUNT(DISTINCT user_id) FILTER (
                WHERE chat_type IN ('group', 'supergroup', 'channel')
            ) AS audience_chats,
            COUNT(*) FILTER (
                WHERE chat_type IN ('group', 'supergroup', 'channel')
            ) AS audience_chats_total,
            COUNT(DISTINCT user_id) FILTER (
                WHERE chat_type IN ('sender', 'private') OR chat_type IS NULL
            ) AS audience_dm,
            COUNT(*) FILTER (
                WHERE chat_type IN ('sender', 'private') OR chat_type IS NULL
            ) AS audience_dm_total,
            COUNT(DISTINCT user_id) FILTER (WHERE chat_type = 'group') AS audience_group,
            COUNT(DISTINCT user_id) FILTER (WHERE chat_type = 'supergroup') AS audience_supergroup,
            COUNT(DISTINCT user_id) FILTER (WHERE chat_type = 'channel') AS audience_channel
        FROM inline_uses
        """,
        today, yesterday,
    )
    return dict(row)


# --- GIF cache ---

# --- Расширенные метрики (генерация / прирост / топы) ---

async def get_generation_stats(pool: Pool) -> dict:
    """Сводка по генерациям гифок (inline_uses) по окнам времени."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                                       AS total,
            COUNT(*) FILTER (WHERE chosen_at::date = $1)                   AS today,
            COUNT(*) FILTER (WHERE chosen_at::date = $2)                   AS yesterday,
            COUNT(*) FILTER (WHERE chosen_at >= NOW() - INTERVAL '7 days')  AS week,
            COUNT(*) FILTER (WHERE chosen_at >= NOW() - INTERVAL '30 days') AS month,
            COUNT(DISTINCT user_id)                                        AS unique_total,
            COUNT(DISTINCT user_id) FILTER (WHERE chosen_at::date = $1)    AS unique_today,
            COUNT(DISTINCT user_id) FILTER (WHERE chosen_at >= NOW() - INTERVAL '7 days') AS unique_week,
            COUNT(DISTINCT gif_id)                                         AS templates_used
        FROM inline_uses
        """,
        today, yesterday,
    )
    return dict(row)


async def get_generation_by_day(pool: Pool, days: int = 7) -> list[dict]:
    """Генераций по дням за последние `days` дней (включая дни с нулём)."""
    rows = await pool.fetch(
        """
        SELECT d::date AS day, COALESCE(c.cnt, 0) AS cnt
        FROM generate_series(
            (NOW() - ($1::int - 1) * INTERVAL '1 day')::date, NOW()::date, INTERVAL '1 day'
        ) d
        LEFT JOIN (
            SELECT chosen_at::date AS day, COUNT(*) AS cnt
            FROM inline_uses
            WHERE chosen_at >= (NOW() - ($1::int - 1) * INTERVAL '1 day')::date
            GROUP BY chosen_at::date
        ) c ON c.day = d::date
        ORDER BY day
        """,
        days,
    )
    return [dict(r) for r in rows]


async def get_top_generators(pool: Pool, limit: int = 10, days: int | None = None) -> list[dict]:
    """Топ юзеров по числу сгенерированных гифок. days=None → за всё время."""
    where = "WHERE iu.chosen_at >= NOW() - ($2::int * INTERVAL '1 day')" if days else ""
    params = [limit] + ([days] if days else [])
    rows = await pool.fetch(
        f"""
        SELECT iu.user_id, COUNT(*) AS cnt,
               u.username, u.first_name
        FROM inline_uses iu
        LEFT JOIN users u ON u.id = iu.user_id
        {where}
        GROUP BY iu.user_id, u.username, u.first_name
        ORDER BY cnt DESC
        LIMIT $1
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_new_users_by_day(pool: Pool, days: int = 7) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT d::date AS day, COALESCE(c.cnt, 0) AS cnt
        FROM generate_series(
            (NOW() - ($1::int - 1) * INTERVAL '1 day')::date, NOW()::date, INTERVAL '1 day'
        ) d
        LEFT JOIN (
            SELECT created_at::date AS day, COUNT(*) AS cnt
            FROM users
            WHERE created_at >= (NOW() - ($1::int - 1) * INTERVAL '1 day')::date
            GROUP BY created_at::date
        ) c ON c.day = d::date
        ORDER BY day
        """,
        days,
    )
    return [dict(r) for r in rows]


async def get_growth_stats(pool: Pool) -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE created_at::date = $1)                   AS new_today,
            COUNT(*) FILTER (WHERE created_at::date = $2)                   AS new_yesterday,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days')  AS new_week,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS new_month,
            COUNT(*) FILTER (WHERE is_premium = TRUE)                       AS premium
        FROM users
        """,
        today, yesterday,
    )
    return dict(row)


async def get_organic_growth(pool: Pool) -> dict:
    """Саморост: новые юзеры, пришедшие НЕ по рекламной ссылке (органика), против
    привлечённых рекламой. «Из рекламы» = у юзера есть хоть одна запись в link_visits;
    «органика» = записей нет. Считаем за сегодня / 7д / 30д / всё время."""
    row = await pool.fetchrow(
        """
        WITH lv AS (SELECT DISTINCT user_id FROM link_visits)
        SELECT
            COUNT(*)                                                                          AS total,
            COUNT(*) FILTER (WHERE lv.user_id IS NULL)                                        AS organic_total,
            COUNT(*) FILTER (WHERE u.created_at::date = NOW()::date)                           AS new_today,
            COUNT(*) FILTER (WHERE u.created_at::date = NOW()::date AND lv.user_id IS NULL)     AS organic_today,
            COUNT(*) FILTER (WHERE u.created_at >= NOW() - INTERVAL '7 days')                  AS new_week,
            COUNT(*) FILTER (WHERE u.created_at >= NOW() - INTERVAL '7 days' AND lv.user_id IS NULL)  AS organic_week,
            COUNT(*) FILTER (WHERE u.created_at >= NOW() - INTERVAL '30 days')                 AS new_month,
            COUNT(*) FILTER (WHERE u.created_at >= NOW() - INTERVAL '30 days' AND lv.user_id IS NULL) AS organic_month
        FROM users u
        LEFT JOIN lv ON lv.user_id = u.id
        """
    )
    return dict(row)


async def get_top_templates(pool: Pool, limit: int = 5) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT title, use_count, file_type
        FROM templates
        WHERE is_active = TRUE
        ORDER BY use_count DESC, created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def get_links_overview(pool: Pool) -> dict:
    """Сводная метрика по ВСЕМ рекламным ссылкам разом."""
    today = date.today()
    row = await pool.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM ad_links)                       AS links_total,
            (SELECT COUNT(*) FROM ad_links WHERE is_active)       AS links_active,
            COUNT(*)                                              AS clicks_total,
            COUNT(*) FILTER (WHERE is_first_visit)                AS unique_total,
            COUNT(*) FILTER (WHERE is_bot_suspected = FALSE)      AS live_total,
            COUNT(*) FILTER (WHERE op_passed)                     AS op_passed_total,
            COUNT(*) FILTER (WHERE visited_at::date = $1)         AS clicks_today
        FROM link_visits
        """,
        today,
    )
    return dict(row)


async def get_link_clicks_by_day(pool: Pool, link_id: int, days: int = 7) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT d::date AS day, COALESCE(c.cnt, 0) AS cnt
        FROM generate_series(
            (NOW() - ($2::int - 1) * INTERVAL '1 day')::date, NOW()::date, INTERVAL '1 day'
        ) d
        LEFT JOIN (
            SELECT visited_at::date AS day, COUNT(*) AS cnt
            FROM link_visits
            WHERE link_id = $1
              AND visited_at >= (NOW() - ($2::int - 1) * INTERVAL '1 day')::date
            GROUP BY visited_at::date
        ) c ON c.day = d::date
        ORDER BY day
        """,
        link_id, days,
    )
    return [dict(r) for r in rows]


async def get_cached_gif(pool: Pool, gif_id: str, text_hash: str) -> str | None:
    row = await pool.fetchrow(
        "SELECT telegram_file_id FROM gif_cache WHERE gif_id = $1 AND text_hash = $2",
        gif_id, text_hash,
    )
    return row["telegram_file_id"] if row else None


async def save_gif_cache(pool: Pool, gif_id: str, text_hash: str, telegram_file_id: str):
    await pool.execute(
        """
        INSERT INTO gif_cache (gif_id, text_hash, telegram_file_id)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        """,
        gif_id, text_hash, telegram_file_id,
    )


async def get_cached_gif_batch(pool: Pool, gif_ids: list[str], text_hash: str) -> dict[str, str]:
    rows = await pool.fetch(
        "SELECT gif_id, telegram_file_id FROM gif_cache WHERE gif_id = ANY($1) AND text_hash = $2",
        gif_ids, text_hash,
    )
    return {r["gif_id"]: r["telegram_file_id"] for r in rows}


# --- Broadcasts ---

async def create_broadcast(
    pool: Pool, content_type: str, text: str | None, file_id: str | None,
    buttons: list | None = None, message_data: dict | None = None,
) -> dict:
    total = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = FALSE")
    row = await pool.fetchrow(
        """
        INSERT INTO broadcasts (content_type, text, file_id, total_users, buttons, message_data)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
        RETURNING *
        """,
        content_type, text, file_id, total,
        _json.dumps(buttons or []),
        _json.dumps(message_data) if message_data else None,
    )
    b = dict(row)
    b["buttons"] = _from_jsonb(b.get("buttons")) or []
    b["message_data"] = _from_jsonb(b.get("message_data")) or None
    return b


async def update_broadcast_status(pool: Pool, broadcast_id: int, status: str, sent: int = 0, failed: int = 0):
    if status == "running":
        await pool.execute(
            "UPDATE broadcasts SET status = $1, started_at = NOW() WHERE id = $2",
            status, broadcast_id,
        )
    elif status in ("done", "cancelled"):
        await pool.execute(
            """
            UPDATE broadcasts SET status = $1, sent_count = $2, failed_count = $3,
            finished_at = NOW() WHERE id = $4
            """,
            status, sent, failed, broadcast_id,
        )
    else:
        await pool.execute(
            "UPDATE broadcasts SET sent_count = $1, failed_count = $2 WHERE id = $3",
            sent, failed, broadcast_id,
        )


async def get_broadcast(pool: Pool, broadcast_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM broadcasts WHERE id = $1", broadcast_id)
    if not row:
        return None
    b = dict(row)
    b["buttons"] = _from_jsonb(b.get("buttons")) or []
    b["message_data"] = _from_jsonb(b.get("message_data")) or None
    return b


async def get_recent_broadcasts(pool: Pool, limit: int = 10) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM broadcasts ORDER BY created_at DESC LIMIT $1", limit
    )
    return [dict(r) for r in rows]


# --- Templates ---

async def add_template(
    pool: Pool, file_id: str, file_unique_id: str,
    file_type: str, title: str, tags: list[str] | None = None,
) -> dict:
    # Admin-side: public template. UNIQUE on file_id was removed; instead we look up
    # by stable file_unique_id and update in place, otherwise insert fresh.
    existing = await pool.fetchval(
        """SELECT id FROM templates
           WHERE file_unique_id = $1 AND is_public = TRUE""",
        file_unique_id,
    )
    if existing:
        row = await pool.fetchrow(
            """
            UPDATE templates SET title = $2, tags = $3, file_id = $4, file_type = $5
            WHERE id = $1
            RETURNING *
            """,
            existing, title, tags or [], file_id, file_type,
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO templates (file_id, file_unique_id, file_type, title, tags, is_public)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            RETURNING *
            """,
            file_id, file_unique_id, file_type, title, tags or [],
        )
    return dict(row)


async def update_template_tags(pool: Pool, template_id: int, tags: list[str]):
    await pool.execute(
        "UPDATE templates SET tags = $1::text[] WHERE id = $2",
        tags, template_id,
    )


async def renormalize_all_template_tags(pool: Pool) -> int:
    """Одноразовый проход на старте: привести уже сохранённые теги к канонической
    форме query_parser.extract_emojis (порезать слитные "😂🤣", убрать FE0F).
    Иначе старые теги, записанные до фикса, так и не находились бы поиском."""
    from bot.services.query_parser import extract_emojis
    rows = await pool.fetch("SELECT id, tags FROM templates WHERE tags IS NOT NULL AND tags <> '{}'")
    fixed = 0
    for r in rows:
        old = list(r["tags"])
        new: list[str] = []
        for tag in old:
            for e in extract_emojis(tag):
                if e not in new:
                    new.append(e)
        if new != old:
            await pool.execute("UPDATE templates SET tags = $1::text[] WHERE id = $2", new, r["id"])
            fixed += 1
    return fixed


async def get_template(pool: Pool, template_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM templates WHERE id = $1", template_id)
    return dict(row) if row else None


async def get_active_templates(
    pool: Pool, limit: int = 50, offset: int = 0, user_id: int | None = None,
) -> list[dict]:
    if user_id:
        rows = await pool.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(t.file_unique_id, t.id::text))
                    t.*, p.pinned_at, p.user_id AS pin_uid
                FROM templates t
                LEFT JOIN user_template_pins p ON p.template_id = t.id AND p.user_id = $3
                WHERE t.is_active = TRUE
                  AND (t.is_public = TRUE OR t.submitted_by = $3 OR p.user_id IS NOT NULL)
                ORDER BY
                    COALESCE(t.file_unique_id, t.id::text),
                    CASE WHEN p.user_id IS NOT NULL THEN 0
                         WHEN t.submitted_by = $3 THEN 1
                         ELSE 2 END,
                    t.use_count DESC, t.created_at DESC
            ) sub
            ORDER BY
                CASE WHEN pin_uid IS NOT NULL THEN 0
                     WHEN submitted_by = $3 THEN 1
                     ELSE 2 END,
                pinned_at DESC NULLS LAST,
                CASE WHEN submitted_by = $3 AND pin_uid IS NULL THEN created_at END DESC NULLS LAST,
                use_count DESC, created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset, user_id,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(file_unique_id, id::text)) *
                FROM templates
                WHERE is_active = TRUE AND is_public = TRUE
                ORDER BY COALESCE(file_unique_id, id::text), use_count DESC, created_at DESC
            ) sub
            ORDER BY use_count DESC, created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return [dict(r) for r in rows]


async def get_templates_by_tags(
    pool: Pool, tags: list[str], limit: int = 50, user_id: int | None = None,
    offset: int = 0,
) -> list[dict]:
    if user_id:
        rows = await pool.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(t.file_unique_id, t.id::text))
                    t.*, p.pinned_at, p.user_id AS pin_uid
                FROM templates t
                LEFT JOIN user_template_pins p ON p.template_id = t.id AND p.user_id = $3
                WHERE t.is_active = TRUE AND t.tags && $1
                  AND (t.is_public = TRUE OR t.submitted_by = $3 OR p.user_id IS NOT NULL)
                ORDER BY
                    COALESCE(t.file_unique_id, t.id::text),
                    CASE WHEN p.user_id IS NOT NULL THEN 0
                         WHEN t.submitted_by = $3 THEN 1
                         ELSE 2 END,
                    t.use_count DESC, t.created_at DESC
            ) sub
            ORDER BY
                CASE WHEN pin_uid IS NOT NULL THEN 0
                     WHEN submitted_by = $3 THEN 1
                     ELSE 2 END,
                pinned_at DESC NULLS LAST,
                CASE WHEN submitted_by = $3 AND pin_uid IS NULL THEN created_at END DESC NULLS LAST,
                use_count DESC
            LIMIT $2 OFFSET $4
            """,
            tags, limit, user_id, offset,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(file_unique_id, id::text)) *
                FROM templates
                WHERE is_active = TRUE AND tags && $1 AND is_public = TRUE
                ORDER BY COALESCE(file_unique_id, id::text), use_count DESC, created_at DESC
            ) sub
            ORDER BY use_count DESC
            LIMIT $2 OFFSET $3
            """,
            tags, limit, offset,
        )
    return [dict(r) for r in rows]


async def search_templates(pool: Pool, query: str, limit: int = 50) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM templates
        WHERE is_active = TRUE AND title ILIKE $1
        ORDER BY use_count DESC
        LIMIT $2
        """,
        f"%{query}%", limit,
    )
    return [dict(r) for r in rows]


async def get_all_templates_admin(pool: Pool, limit: int = 8, offset: int = 0) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM templates ORDER BY use_count DESC, created_at DESC LIMIT $1 OFFSET $2",
        limit, offset,
    )
    return [dict(r) for r in rows]


async def get_templates_count(
    pool: Pool, active_only: bool = False, user_id: int | None = None,
) -> int:
    if active_only and user_id:
        return await pool.fetchval(
            """
            SELECT COUNT(*) FROM templates t
            LEFT JOIN user_template_pins p ON p.template_id = t.id AND p.user_id = $1
            WHERE t.is_active = TRUE AND (t.is_public = TRUE OR t.submitted_by = $1 OR p.user_id IS NOT NULL)
            """,
            user_id,
        )
    if active_only:
        return await pool.fetchval(
            "SELECT COUNT(*) FROM templates WHERE is_active = TRUE AND is_public = TRUE"
        )
    return await pool.fetchval("SELECT COUNT(*) FROM templates")


async def toggle_template(pool: Pool, template_id: int, is_active: bool):
    await pool.execute("UPDATE templates SET is_active = $1 WHERE id = $2", is_active, template_id)


async def delete_template(pool: Pool, template_id: int):
    await pool.execute("DELETE FROM templates WHERE id = $1", template_id)


async def add_user_template(
    pool: Pool, file_id: str, file_unique_id: str, file_type: str,
    title: str, submitted_by: int, is_public: bool,
    content_hash: str | None = None,
) -> dict | None:
    """Returns None if duplicate:
    - public: same file (matched by file_unique_id OR content_hash) already exists as any
      public template from ANY user. content_hash (sha256 of the raw bytes) catches the
      case where a different user re-uploaded the same gif — Telegram then assigns a fresh
      file_unique_id, so file_unique_id alone would let the cross-user copy through.
    - personal: same user already has this file_unique_id as personal template
    """
    if is_public:
        # If there's a rejected record for this file, reuse it (update to pending)
        # so we don't create a duplicate and don't hit the unique constraint.
        rejected_id = await pool.fetchval(
            """SELECT id FROM templates
               WHERE is_public = TRUE AND moderation_status = 'rejected'
                 AND (file_unique_id = $1 OR ($2::text IS NOT NULL AND content_hash = $2))""",
            file_unique_id, content_hash,
        )
        if rejected_id:
            row = await pool.fetchrow(
                """UPDATE templates
                   SET file_id=$1, file_type=$2, title=$3, submitted_by=$4, content_hash=$6,
                       moderation_status='pending', is_active=FALSE, moderation_comment=NULL
                   WHERE id=$5 RETURNING *""",
                file_id, file_type, title, submitted_by, rejected_id, content_hash,
            )
            return dict(row)
        # Only one public copy of a given file allowed (pending or approved), regardless
        # of who submitted it — matched by stable file id OR by content hash.
        existing = await pool.fetchval(
            """SELECT id FROM templates
               WHERE is_public = TRUE
                 AND (file_unique_id = $1 OR ($2::text IS NOT NULL AND content_hash = $2))""",
            file_unique_id, content_hash,
        )
    else:
        # Only one personal copy of a given file per user (independent from public)
        existing = await pool.fetchval(
            """SELECT id FROM templates
               WHERE file_unique_id = $1 AND submitted_by = $2 AND is_public = FALSE""",
            file_unique_id, submitted_by,
        )
    if existing:
        return None
    import asyncpg
    status = "pending" if is_public else "approved"
    active = not is_public
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO templates
              (file_id, file_unique_id, file_type, title, tags,
               submitted_by, is_public, moderation_status, is_active, content_hash)
            VALUES ($1,$2,$3,$4,'{}', $5,$6,$7,$8,$9)
            RETURNING *
            """,
            file_id, file_unique_id, file_type, title,
            submitted_by, is_public, status, active, content_hash,
        )
    except asyncpg.UniqueViolationError:
        return None
    return dict(row)


async def pin_template_for_user(pool: Pool, user_id: int, template_id: int) -> dict | None:
    """Add a template to the user's personal 'pinned' list so it appears first in their inline list."""
    src = await pool.fetchrow("SELECT * FROM templates WHERE id = $1 AND is_active = TRUE", template_id)
    if not src:
        return None
    await pool.execute(
        "INSERT INTO user_template_pins (user_id, template_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, template_id,
    )
    return dict(src)


async def get_pending_templates(pool: Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM templates WHERE moderation_status = 'pending' ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def approve_template(pool: Pool, template_id: int):
    await pool.execute(
        "UPDATE templates SET is_active = TRUE, moderation_status = 'approved' WHERE id = $1",
        template_id,
    )


async def reject_template(pool: Pool, template_id: int, comment: str | None = None):
    await pool.execute(
        """
        UPDATE templates
        SET is_active = FALSE, moderation_status = 'rejected', moderation_comment = $2
        WHERE id = $1
        """,
        template_id, comment,
    )


async def increment_template_use_count(pool: Pool, template_id: int):
    await pool.execute("UPDATE templates SET use_count = use_count + 1 WHERE id = $1", template_id)


# --- Bot messages & buttons ---

import json as _json
import re as _re


def _from_jsonb(val) -> list | dict:
    """asyncpg 0.27+ returns JSONB as Python objects; older versions return str."""
    if val is None:
        return []
    if isinstance(val, (list, dict)):
        return val
    return _json.loads(val)


# HTML-теги, которые Телеграм умеет как message entities.
_HTML_ENTITY_TAGS = {
    "b": "bold", "strong": "bold",
    "i": "italic", "em": "italic",
    "u": "underline", "ins": "underline",
    "s": "strikethrough", "strike": "strikethrough", "del": "strikethrough",
    "code": "code", "pre": "pre",
    "a": "text_link",
    "tg-spoiler": "spoiler",
    "blockquote": "blockquote",
}
_TAG_RE = _re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)((?:\s[^>]*)?)>")
_HREF_RE = _re.compile(r"""href\s*=\s*["']([^"']*)["']""")


def _u16(s: str) -> int:
    """Длина строки в UTF-16 code units — в них Телеграм считает offset/length энтитей."""
    return len(s.encode("utf-16-le")) // 2


def _html_to_entities(text: str, base_entities: list) -> tuple[str, list]:
    """Превратить HTML-теги (<b>, <i>, <a href>…) в message entities и слить их с уже
    имеющимися (прем-эмодзи и пр.). Возвращает (чистый_текст_без_тегов, список_энтитей).

    Зачем: Телеграм НЕ принимает parse_mode=HTML вместе с entities. У менюшек с
    прем-эмодзи entities есть всегда, поэтому раньше голые <b> уходили как обычный
    текст и показывались тегами. Теперь теги конвертируются в энтити, а офсеты
    base_entities сдвигаются на вырезанные теги. Всё в UTF-16, как требует Bot API."""
    base_entities = list(base_entities or [])
    if "<" not in (text or ""):
        return text or "", base_entities

    out: list[str] = []
    entities: list[dict] = []
    stack: list[tuple] = []           # (etype, clean_start_u16, extra)
    cuts: list[tuple[int, int]] = []  # (raw_start_u16, removed_len_u16) — для сдвига base
    raw_u16 = clean_u16 = 0
    i, n = 0, len(text)
    while i < n:
        m = _TAG_RE.match(text, i) if text[i] == "<" else None
        if not m:
            ch = text[i]
            c = _u16(ch)
            out.append(ch)
            clean_u16 += c
            raw_u16 += c
            i += 1
            continue
        name = m.group(1).lower()
        etype = _HTML_ENTITY_TAGS.get(name)
        full = m.group(0)
        flen = _u16(full)
        if etype is None:
            # неизвестный тег — оставляем как есть (это просто текст)
            out.append(full)
            clean_u16 += flen
            raw_u16 += flen
            i = m.end()
            continue
        cuts.append((raw_u16, flen))
        if full.startswith("</"):
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == etype:
                    et, start, extra = stack.pop(k)
                    if clean_u16 > start:
                        ent = {"type": et, "offset": start, "length": clean_u16 - start}
                        ent.update(extra)
                        entities.append(ent)
                    break
        else:
            extra = {}
            if etype == "text_link":
                hm = _HREF_RE.search(m.group(2) or "")
                extra["url"] = hm.group(1) if hm else ""
            stack.append((etype, clean_u16, extra))
        raw_u16 += flen
        i = m.end()

    clean = "".join(out)
    for be in base_entities:
        o = be.get("offset", 0)
        shift = sum(l for (s, l) in cuts if s < o)
        nb = dict(be)
        nb["offset"] = o - shift
        entities.append(nb)
    entities.sort(key=lambda e: (e["offset"], -e["length"]))
    return clean, entities


_MSG_DEFAULTS: dict[str, str] = {
    "welcome": (
        "🌐 Привет! Я бот, с помощью которого можно накладывать текст на мемные шаблоны.\n\n"
        "Чтобы разместить текст в нижней части шаблона, разделите фразы точкой.\n"
        "Подробная видеоинструкция по работе @гифыч прикреплена к этому сообщению."
    ),
    "op_required": "👋 Привет! Для доступа подпишись на наши каналы:",
    "op_passed": "✅ Отлично, {name}!\n\n🌐 Используй @гифыч в любом чате и выбери шаблон.",
    "meme_send_text": "✏️ Напиши текст для наложения (до 100 символов):",
    "settings": "⚙️ Настройки",
    "premium": "💎 <b>Гифыч Premium</b>\n\nОтключи рекламу и показы навсегда.",
    "tpl.add_choose_type": (
        "🖼 Каким способом добавить шаблон?\n\n"
        "⚡ <b>Быстро (только для вас)</b> — сразу доступен, не проходит модерацию.\n\n"
        "🌐 <b>Публичный</b> — проходит модерацию, после одобрения виден всем."
    ),
}

_BTN_DEFAULTS: dict[str, list] = {
    "main_menu": [
        [{"text": "🖼 Добавить шаблон", "callback_data": "template:add_user"}],
        [{"text": "💎 Гифыч Premium", "callback_data": "premium:open"}],
        [
            {"text": "📰 Новости ↗", "url": "https://t.me/your_news_channel"},
            {"text": "📁 Шаблоны ↗", "url": "https://t.me/your_media_channel"},
        ],
        [{"text": "⚙️ Настройки", "callback_data": "settings:open"}],
    ],
    "settings_menu": [
        [{"text": "🌐 Язык", "callback_data": "settings:language"}],
        [{"text": "🤖 Оставить жалобу ↗", "url": "https://t.me/your_support_channel"}],
        [{"text": "Купить рекламу в Гифыч ↗", "url": "https://t.me/your_ads_channel"}],
        [{"text": "⬅ Назад", "callback_data": "settings:back"}],
    ],
    "premium_menu": [
        [{"text": "💎 1 месяц — 9 ⭐", "callback_data": "premium:buy:1m:9"}],
        [{"text": "💎 6 месяцев — 15 ⭐", "callback_data": "premium:buy:6m:15"}],
        [{"text": "💎 12 месяцев — 27 ⭐", "callback_data": "premium:buy:12m:27"}],
        [{"text": "💎 Навсегда — 49 ⭐", "callback_data": "premium:buy:999y:49"}],
        [
            {"text": "⭐ Купить звёзды ↗", "url": "https://t.me/stars"},
            {"text": "🎁 Подарить", "callback_data": "premium:gift"},
        ],
        [{"text": "⬅ Назад", "callback_data": "premium:back"}],
    ],
    # Экран «Каким способом добавить шаблон?» — кнопки редактируются ПОШТУЧНО
    # (текст/цвет/прем-иконка) через админку, как у остальных менюшек.
    "tpl_choose_menu": [
        [{"text": "⚡ Быстро добавить", "callback_data": "utpl:quick"}],
        [{"text": "🌐 Публичный шаблон", "callback_data": "utpl:public"}],
        [{"text": "❌ Отмена", "callback_data": "utpl:cancel"}],
    ],
}

# English fallbacks for menu texts/buttons so selecting English actually switches the
# whole UI even before an admin enters per-language overrides in the admin panel.
_MSG_DEFAULTS_EN: dict[str, str] = {
    "welcome": (
        "🌐 Hi! I'm a bot that overlays text onto meme templates.\n\n"
        "To put text at the bottom of a template, separate phrases with a period.\n"
        "A detailed video guide for @гифыч is attached to this message."
    ),
    "op_required": "👋 Hi! Subscribe to our channels to get access:",
    "op_passed": "✅ Great, {name}!\n\n🌐 Use @гифыч in any chat and pick a template.",
    "meme_send_text": "✏️ Type the overlay text (up to 100 characters):",
    "settings": "⚙️ Settings",
    "premium": "💎 <b>Gifych Premium</b>\n\nTurn off ads and promos forever.",
    "tpl.add_choose_type": (
        "🖼 How do you want to add a template?\n\n"
        "⚡ <b>Quick (only for you)</b> — available instantly, no moderation.\n\n"
        "🌐 <b>Public</b> — goes through moderation, visible to everyone once approved."
    ),
}

_BTN_DEFAULTS_EN: dict[str, list] = {
    "main_menu": [
        [{"text": "🖼 Add template", "callback_data": "template:add_user"}],
        [{"text": "💎 Gifych Premium", "callback_data": "premium:open"}],
        [
            {"text": "📰 News ↗", "url": "https://t.me/your_news_channel"},
            {"text": "📁 Templates ↗", "url": "https://t.me/your_media_channel"},
        ],
        [{"text": "⚙️ Settings", "callback_data": "settings:open"}],
    ],
    "settings_menu": [
        [{"text": "🌐 Language", "callback_data": "settings:language"}],
        [{"text": "🤖 Leave a complaint ↗", "url": "https://t.me/your_support_channel"}],
        [{"text": "Buy ads in Gifych ↗", "url": "https://t.me/your_ads_channel"}],
        [{"text": "⬅ Back", "callback_data": "settings:back"}],
    ],
    "premium_menu": [
        [{"text": "💎 1 month — 9 ⭐", "callback_data": "premium:buy:1m:9"}],
        [{"text": "💎 6 months — 15 ⭐", "callback_data": "premium:buy:6m:15"}],
        [{"text": "💎 12 months — 27 ⭐", "callback_data": "premium:buy:12m:27"}],
        [{"text": "💎 Forever — 49 ⭐", "callback_data": "premium:buy:999y:49"}],
        [
            {"text": "⭐ Buy Stars ↗", "url": "https://t.me/stars"},
            {"text": "🎁 Gift", "callback_data": "premium:gift"},
        ],
        [{"text": "⬅ Back", "callback_data": "premium:back"}],
    ],
    "tpl_choose_menu": [
        [{"text": "⚡ Quick add", "callback_data": "utpl:quick"}],
        [{"text": "🌐 Public template", "callback_data": "utpl:public"}],
        [{"text": "❌ Cancel", "callback_data": "utpl:cancel"}],
    ],
}


def _msg_default(key: str, lang: str) -> str:
    if lang == "en" and key in _MSG_DEFAULTS_EN:
        return _MSG_DEFAULTS_EN[key]
    return _MSG_DEFAULTS.get(key, "")


def _btn_default(key: str, lang: str) -> list:
    if lang == "en" and key in _BTN_DEFAULTS_EN:
        return _BTN_DEFAULTS_EN[key]
    return _BTN_DEFAULTS.get(key, [])


def _msg_payload(text, entities, file_id, file_type) -> dict:
    """Единый вид bot_message для рендера: HTML-теги в тексте превращаем в энтити и
    сливаем с прем-эмодзи, чтобы менюшка показывалась с форматированием И эмодзи разом
    (Телеграм не умеет parse_mode=HTML + entities одновременно)."""
    clean, ents = _html_to_entities(text or "", entities or [])
    return {"text": clean, "entities": ents, "file_id": file_id, "file_type": file_type}


async def get_bot_message(pool: Pool, key: str, lang: str = "ru") -> dict:
    row = await pool.fetchrow(
        "SELECT text, entities, file_id, file_type FROM bot_messages WHERE key = $1 AND lang = $2",
        key, lang,
    )
    # No per-language override. If we ship a default for this language, prefer its TEXT
    # over the RU DB row (иначе англ. юзер увидел бы русский текст), но МЕДИА наследуем
    # из RU-строки — видео/гиф у менюшки обычно языконезависимое, не теряем его.
    if not row and lang == "en" and key in _MSG_DEFAULTS_EN:
        ru_media = await pool.fetchrow(
            "SELECT file_id, file_type FROM bot_messages WHERE key = $1 AND lang = 'ru'", key,
        )
        return _msg_payload(
            _MSG_DEFAULTS_EN[key], [],
            ru_media["file_id"] if ru_media else None,
            ru_media["file_type"] if ru_media else None,
        )
    if not row and lang != "ru":
        row = await pool.fetchrow(
            "SELECT text, entities, file_id, file_type FROM bot_messages WHERE key = $1 AND lang = 'ru'",
            key,
        )
    if row:
        return _msg_payload(
            row["text"], _from_jsonb(row["entities"]), row["file_id"], row["file_type"],
        )
    return _msg_payload(_msg_default(key, lang), [], None, None)


async def set_bot_message(pool: Pool, key: str, text: str, entities: list, lang: str = "ru"):
    # Текст/энтити обновляем, медиа НЕ трогаем (оно живёт своей кнопкой в редакторе).
    await pool.execute(
        """
        INSERT INTO bot_messages (key, lang, text, entities) VALUES ($1, $2, $3, $4::jsonb)
        ON CONFLICT (key, lang) DO UPDATE SET text = EXCLUDED.text, entities = EXCLUDED.entities
        """,
        key, lang, text, _json.dumps(entities),
    )


async def set_bot_message_media(pool: Pool, key: str, file_id: str | None,
                                file_type: str | None, lang: str = "ru"):
    """Привязать/снять медиа (видео/animation/photo) у менюшки. file_id=None → снять.
    Текст/энтити НЕ затираем."""
    if file_id is None:
        await pool.execute(
            "UPDATE bot_messages SET file_id = NULL, file_type = NULL WHERE key = $1 AND lang = $2",
            key, lang,
        )
        return
    # На вставке (новая строка) текст/энтити дефолтные; на конфликте трогаем ТОЛЬКО
    # медиа, поэтому существующий текст не теряется. Подзапросы за text/entities тут не
    # нужны — а их $1 в двух ролях (varchar-значение и text в COALESCE) раньше ломал
    # вывод типа параметра (AmbiguousParameterError: text и character varying).
    await pool.execute(
        """
        INSERT INTO bot_messages (key, lang, text, entities, file_id, file_type)
        VALUES ($1, $2, '', '[]'::jsonb, $3, $4)
        ON CONFLICT (key, lang) DO UPDATE SET file_id = EXCLUDED.file_id, file_type = EXCLUDED.file_type
        """,
        key, lang, file_id, file_type,
    )


async def get_all_bot_messages(pool: Pool, lang: str = "ru") -> list[dict]:
    rows = await pool.fetch(
        "SELECT key, text FROM bot_messages WHERE lang = $1 ORDER BY key", lang,
    )
    result = list(_MSG_DEFAULTS.keys())
    db_keys = {r["key"]: r["text"] for r in rows}
    out = []
    for k in result:
        out.append({"key": k, "text": db_keys.get(k, _MSG_DEFAULTS[k]), "from_db": k in db_keys})
    for k, t in db_keys.items():
        if k not in result:
            out.append({"key": k, "text": t, "from_db": True})
    return out


async def get_bot_buttons(pool: Pool, key: str, lang: str = "ru") -> list:
    row = await pool.fetchrow(
        "SELECT rows FROM bot_buttons WHERE key = $1 AND lang = $2", key, lang,
    )
    if not row and lang == "en" and key in _BTN_DEFAULTS_EN:
        return _BTN_DEFAULTS_EN[key]
    if not row and lang != "ru":
        row = await pool.fetchrow(
            "SELECT rows FROM bot_buttons WHERE key = $1 AND lang = 'ru'", key,
        )
    if row:
        return _from_jsonb(row["rows"])
    return _btn_default(key, lang)


async def set_bot_buttons(pool: Pool, key: str, rows: list, lang: str = "ru"):
    await pool.execute(
        """
        INSERT INTO bot_buttons (key, lang, rows) VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (key, lang) DO UPDATE SET rows = EXCLUDED.rows
        """,
        key, lang, _json.dumps(rows),
    )


async def get_user_recent_gifs(pool: Pool, user_id: int) -> list[str]:
    rows = await pool.fetch(
        "SELECT DISTINCT gif_id FROM inline_uses WHERE user_id = $1 ORDER BY gif_id LIMIT 10",
        user_id,
    )
    return [r["gif_id"] for r in rows]


# --- Ad placements ---

async def create_placement(
    pool: Pool, name: str, caption_text: str | None,
    button_text: str | None, button_url: str | None, starts_at, ends_at,
    limit_count: int = 3, limit_window_sec: int = 3600,
) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO ad_placements
           (name, caption_text, button_text, button_url, starts_at, ends_at, limit_count, limit_window_sec)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *""",
        name, caption_text, button_text, button_url, starts_at, ends_at, limit_count, limit_window_sec,
    )
    return dict(row)


async def get_all_placements(pool: Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM ad_placements ORDER BY created_at DESC")
    return [dict(r) for r in rows]


async def get_placements_stats(pool: Pool) -> dict:
    total = await pool.fetchval("SELECT COUNT(*) FROM ad_placements")
    active = await pool.fetchval(
        "SELECT COUNT(*) FROM ad_placements WHERE is_active = TRUE AND NOW() BETWEEN starts_at AND ends_at"
    )
    rows = await pool.fetch(
        """
        SELECT p.*,
            (SELECT COUNT(*) FROM placement_sends s WHERE s.placement_id = p.id) AS total_sends,
            (SELECT COUNT(DISTINCT user_id) FROM placement_sends s WHERE s.placement_id = p.id) AS unique_users,
            (SELECT COUNT(DISTINCT chat_instance) FROM placement_sends s
             WHERE s.placement_id = p.id AND chat_instance IS NOT NULL) AS unique_chats
        FROM ad_placements p ORDER BY p.created_at DESC LIMIT 8
        """
    )
    return {"total": total, "active": active, "recent": [dict(r) for r in rows]}


async def get_active_placements(pool: Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM ad_placements WHERE is_active = TRUE AND NOW() BETWEEN starts_at AND ends_at"
    )
    return [dict(r) for r in rows]


async def get_eligible_placements_for_user(pool: Pool, user_id: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT p.* FROM ad_placements p
        WHERE p.is_active = TRUE AND NOW() BETWEEN p.starts_at AND p.ends_at
          AND COALESCE(p.limit_count, 0) > 0
          AND (
              SELECT COUNT(*) FROM placement_sends s
              WHERE s.placement_id = p.id AND s.user_id = $1
                AND s.created_at >= NOW() - (COALESCE(p.limit_window_sec, 3600) || ' seconds')::INTERVAL
          ) < p.limit_count
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def get_placement(pool: Pool, placement_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM ad_placements WHERE id = $1", placement_id)
    return dict(row) if row else None


async def record_placement_send(pool: Pool, placement_id: int, user_id: int, chat_instance: str | None = None):
    await pool.execute(
        "INSERT INTO placement_sends (placement_id, user_id, chat_instance) VALUES ($1, $2, $3)",
        placement_id, user_id, chat_instance,
    )


async def toggle_placement(pool: Pool, placement_id: int, is_active: bool):
    await pool.execute("UPDATE ad_placements SET is_active = $1 WHERE id = $2", is_active, placement_id)


async def delete_placement(pool: Pool, placement_id: int):
    await pool.execute("DELETE FROM ad_placements WHERE id = $1", placement_id)


# --- Bot displays ---

async def create_display(
    pool: Pool, name: str, caption_text: str | None,
    file_id: str | None, file_type: str | None,
    button_text: str | None, button_url: str | None,
    sort_order: int = 0,
) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO bot_displays
           (name, caption_text, file_id, file_type, button_text, button_url, sort_order)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
        name, caption_text, file_id, file_type, button_text, button_url, sort_order,
    )
    return dict(row)


async def get_all_displays(pool: Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM bot_displays ORDER BY sort_order ASC, created_at DESC")
    return [dict(r) for r in rows]


async def get_active_displays(pool: Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM bot_displays WHERE is_active = TRUE ORDER BY sort_order ASC, created_at ASC"
    )
    return [dict(r) for r in rows]


async def get_display(pool: Pool, display_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM bot_displays WHERE id = $1", display_id)
    return dict(row) if row else None


async def toggle_display(pool: Pool, display_id: int, is_active: bool):
    await pool.execute("UPDATE bot_displays SET is_active = $1 WHERE id = $2", is_active, display_id)


async def delete_display(pool: Pool, display_id: int):
    await pool.execute("DELETE FROM bot_displays WHERE id = $1", display_id)


async def record_display_view(pool: Pool, user_id: int, display_id: int):
    await pool.execute(
        "INSERT INTO display_views (user_id, display_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, display_id,
    )


async def get_display_views_count(pool: Pool, display_id: int) -> int:
    val = await pool.fetchval("SELECT COUNT(*) FROM display_views WHERE display_id = $1", display_id)
    return val or 0


# --- Dynamic admins ---

async def list_bot_admins(pool: Pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT a.user_id, a.permissions, a.granted_by, a.created_at,
               u.username, u.first_name, u.last_name
        FROM bot_admins a
        LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.created_at DESC
        """
    )
    return [dict(r) for r in rows]


async def get_admin_permissions(pool: Pool, user_id: int) -> list[str] | None:
    """Returns permissions list if user is a dynamic admin, None otherwise."""
    row = await pool.fetchrow("SELECT permissions FROM bot_admins WHERE user_id = $1", user_id)
    if row is None:
        return None
    return list(row["permissions"] or [])


async def add_bot_admin(pool: Pool, user_id: int, permissions: list[str], granted_by: int):
    await pool.execute(
        """
        INSERT INTO bot_admins (user_id, permissions, granted_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE SET permissions = EXCLUDED.permissions
        """,
        user_id, permissions, granted_by,
    )


async def update_admin_permissions(pool: Pool, user_id: int, permissions: list[str]):
    await pool.execute(
        "UPDATE bot_admins SET permissions = $1 WHERE user_id = $2",
        permissions, user_id,
    )


async def remove_bot_admin(pool: Pool, user_id: int):
    await pool.execute("DELETE FROM bot_admins WHERE user_id = $1", user_id)


async def get_all_bot_buttons(pool: Pool, lang: str = "ru") -> list[dict]:
    rows = await pool.fetch(
        "SELECT key, rows FROM bot_buttons WHERE lang = $1 ORDER BY key", lang,
    )
    db_keys = {r["key"]: _from_jsonb(r["rows"]) for r in rows}
    out = []
    all_keys = list(_BTN_DEFAULTS.keys())
    for k in all_keys:
        out.append({"key": k, "rows": db_keys.get(k, _BTN_DEFAULTS[k]), "from_db": k in db_keys})
    for k, v in db_keys.items():
        if k not in all_keys:
            out.append({"key": k, "rows": v, "from_db": True})
    return out


# ===== Languages & i18n =====

async def get_languages(pool: Pool, active_only: bool = False) -> list[dict]:
    where = "WHERE is_active = TRUE" if active_only else ""
    rows = await pool.fetch(
        f"SELECT code, name, flag_emoji, is_active, sort_order FROM languages {where} ORDER BY sort_order, code"
    )
    return [dict(r) for r in rows]


async def get_language(pool: Pool, code: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT code, name, flag_emoji, is_active, sort_order FROM languages WHERE code = $1",
        code,
    )
    return dict(row) if row else None


async def add_language(pool: Pool, code: str, name: str, flag_emoji: str = "🌐") -> dict | None:
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO languages (code, name, flag_emoji, sort_order)
            VALUES ($1, $2, $3, COALESCE((SELECT MAX(sort_order)+1 FROM languages), 0))
            RETURNING code, name, flag_emoji, is_active, sort_order
            """,
            code, name, flag_emoji,
        )
        return dict(row) if row else None
    except Exception:
        return None


async def update_language(pool: Pool, code: str, name: str | None = None,
                          flag_emoji: str | None = None, is_active: bool | None = None) -> None:
    sets = []
    args: list = []
    idx = 1
    if name is not None:
        sets.append(f"name = ${idx}"); args.append(name); idx += 1
    if flag_emoji is not None:
        sets.append(f"flag_emoji = ${idx}"); args.append(flag_emoji); idx += 1
    if is_active is not None:
        sets.append(f"is_active = ${idx}"); args.append(is_active); idx += 1
    if not sets:
        return
    args.append(code)
    await pool.execute(
        f"UPDATE languages SET {', '.join(sets)} WHERE code = ${idx}", *args,
    )


async def delete_language(pool: Pool, code: str) -> None:
    if code == "ru":
        return
    await pool.execute("DELETE FROM bot_messages WHERE lang = $1", code)
    await pool.execute("DELETE FROM bot_buttons WHERE lang = $1", code)
    await pool.execute("DELETE FROM i18n_strings WHERE lang = $1", code)
    await pool.execute("UPDATE users SET lang = 'ru' WHERE lang = $1", code)
    await pool.execute("DELETE FROM languages WHERE code = $1", code)


async def get_user_lang(pool: Pool, user_id: int) -> str:
    row = await pool.fetchrow("SELECT lang FROM users WHERE id = $1", user_id)
    if not row or not row["lang"]:
        return "ru"
    lang_row = await pool.fetchrow(
        "SELECT is_active FROM languages WHERE code = $1", row["lang"],
    )
    if not lang_row or not lang_row["is_active"]:
        return "ru"
    return row["lang"]


async def set_user_lang(pool: Pool, user_id: int, lang: str) -> None:
    await pool.execute("UPDATE users SET lang = $1 WHERE id = $2", lang, user_id)


async def get_i18n_string(pool: Pool, key: str, lang: str = "ru") -> str | None:
    row = await pool.fetchrow(
        "SELECT value FROM i18n_strings WHERE key = $1 AND lang = $2", key, lang,
    )
    if not row and lang != "ru":
        row = await pool.fetchrow(
            "SELECT value FROM i18n_strings WHERE key = $1 AND lang = 'ru'", key,
        )
    return row["value"] if row else None


async def set_i18n_string(pool: Pool, key: str, lang: str, value: str) -> None:
    await pool.execute(
        """
        INSERT INTO i18n_strings (key, lang, value) VALUES ($1, $2, $3)
        ON CONFLICT (key, lang) DO UPDATE SET value = EXCLUDED.value
        """,
        key, lang, value,
    )


async def get_all_i18n_strings(pool: Pool, lang: str = "ru") -> list[dict]:
    rows = await pool.fetch(
        "SELECT key, value FROM i18n_strings WHERE lang = $1 ORDER BY key", lang,
    )
    return [dict(r) for r in rows]


async def seed_i18n_defaults(pool: Pool, defaults: dict) -> None:
    """Insert missing default UI strings into i18n_strings, skipping existing rows.

    Accepts either the legacy flat {key: value} (seeded as RU) or the new
    {lang: {key: value}} shape so every shipped language (ru, en, …) is seeded."""
    if defaults and all(isinstance(v, dict) for v in defaults.values()):
        by_lang = defaults
    else:
        by_lang = {"ru": defaults}
    for lang, mapping in by_lang.items():
        for key, value in mapping.items():
            await pool.execute(
                """
                INSERT INTO i18n_strings (key, lang, value) VALUES ($1, $2, $3)
                ON CONFLICT (key, lang) DO NOTHING
                """,
                key, lang, value,
            )
