import csv
import io
from datetime import datetime
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, BufferedInputFile

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import (
    get_users_count, get_placements_stats, get_users_export, get_inline_stats,
    get_all_displays, get_display_views_count,
    get_generation_stats, get_generation_by_day, get_top_generators,
    get_new_users_by_day, get_growth_stats, get_top_templates, get_links_overview,
)
from bot.keyboards.admin import stats_menu, database_menu

router = Router()


from bot.services.permissions import has_permission, PERM_STATS, PERM_DATABASE


async def can_stats(user_id: int) -> bool:
    return await has_permission(user_id, PERM_STATS)


async def can_db(user_id: int) -> bool:
    return await has_permission(user_id, PERM_DATABASE)


def _build_db_text(stats: dict) -> str:
    total = stats["total"] or 0
    male_pct = round(stats["male"] / total * 100) if total else 0
    female_pct = round(stats["female"] / total * 100) if total else 0
    alive_pct = round(stats["active_24h"] / total * 100) if total else 0

    return (
        f"👥 <b>База пользователей</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"Заблокировали: <b>{stats['blocked']}</b>\n\n"
        f"🟢 Онлайн (час): <b>{stats['active_hour']}</b>\n"
        f"🟡 Живые (24ч): <b>{stats['active_24h']}</b> ({alive_pct}%)\n"
        f"Активных сегодня: <b>{stats['active_today']}</b>\n"
        f"Активных вчера: <b>{stats['active_yesterday']}</b>\n\n"
        f"Новых сегодня: <b>{stats['new_today']}</b>\n\n"
        f"👫 Пол:\n"
        f"  М: {stats['male']} ({male_pct}%)\n"
        f"  Ж: {stats['female']} ({female_pct}%)"
    )


_SPARK = "▁▂▃▄▅▆▇█"


def _fmt(n) -> str:
    """1234567 -> '1 234 567'."""
    return f"{int(n or 0):,}".replace(",", " ")


def _pct(part, whole) -> int:
    return round((part or 0) / whole * 100) if whole else 0


def _spark(values) -> str:
    vals = [int(v or 0) for v in values]
    hi = max(vals) if vals else 0
    if hi == 0:
        return _SPARK[0] * len(vals)
    return "".join(_SPARK[min(7, round(v / hi * 7))] for v in vals)


def _name(row: dict) -> str:
    name = (row.get("first_name") or "").strip()
    uname = row.get("username")
    if uname:
        return f"{name} (@{uname})" if name else f"@{uname}"
    if name:
        return name
    uid = row.get("user_id")
    return f"id{uid}" if uid else "—"


def _build_stats_text(d: dict) -> str:
    u = d["users"]; g = d["gen"]; gr = d["growth"]; inline = d["inline"]
    pl = d["placements"]; displays = d["displays"]; links = d["links"]
    total_u = u["total"] or 0

    L = [f"📊 <b>Статистика бота</b>",
         f"<i>обновлено {datetime.now().strftime('%d.%m %H:%M')}</i>\n"]

    # — Пользователи (метрики из раздела «БД», теперь в общей стате) —
    L.append(
        f"👥 <b>Пользователи</b>\n"
        f"Всего: <b>{_fmt(total_u)}</b>  ·  премиум: <b>{_fmt(gr['premium'])}</b>  ·  "
        f"🚫 заблочили: <b>{_fmt(u['blocked'])}</b>\n"
        f"🟢 онлайн/час: <b>{_fmt(u['active_hour'])}</b>   "
        f"🟡 живые/24ч: <b>{_fmt(u['active_24h'])}</b> ({_pct(u['active_24h'], total_u)}%)\n"
        f"Активны сегодня: <b>{_fmt(u['active_today'])}</b>  ·  вчера: <b>{_fmt(u['active_yesterday'])}</b>\n"
        f"👫 М: <b>{_fmt(u['male'])}</b> ({_pct(u['male'], total_u)}%)  ·  "
        f"Ж: <b>{_fmt(u['female'])}</b> ({_pct(u['female'], total_u)}%)"
    )

    # — Прирост —
    nbd = d["new_by_day"]
    L.append(
        f"\n📈 <b>Прирост</b>\n"
        f"Сегодня: <b>+{_fmt(gr['new_today'])}</b>  ·  вчера: <b>+{_fmt(gr['new_yesterday'])}</b>\n"
        f"7 дней: <b>+{_fmt(gr['new_week'])}</b>  ·  30 дней: <b>+{_fmt(gr['new_month'])}</b>\n"
        f"<code>{_spark([r['cnt'] for r in nbd])}</code>  (макс/день {_fmt(max([r['cnt'] for r in nbd], default=0))})"
    )

    # — Генерация гифок —
    gbd = d["gen_by_day"]
    L.append(
        f"\n🎬 <b>Генерация гифок</b>\n"
        f"Всего: <b>{_fmt(g['total'])}</b>\n"
        f"Сегодня: <b>{_fmt(g['today'])}</b> ({_fmt(g['unique_today'])} авт.)  ·  "
        f"вчера: <b>{_fmt(g['yesterday'])}</b>\n"
        f"7 дней: <b>{_fmt(g['week'])}</b>  ·  30 дней: <b>{_fmt(g['month'])}</b>\n"
        f"Авторов: всего <b>{_fmt(g['unique_total'])}</b>  ·  за 7д <b>{_fmt(g['unique_week'])}</b>\n"
        f"<code>{_spark([r['cnt'] for r in gbd])}</code>  (макс/день {_fmt(max([r['cnt'] for r in gbd], default=0))})"
    )

    # — Топ-генераторы (7 дней) —
    if d["top_gen_week"]:
        L.append("\n🏆 <b>Топ-генераторы (7д)</b>")
        for i, r in enumerate(d["top_gen_week"], 1):
            L.append(f"  {i}. {_name(r)} — <b>{_fmt(r['cnt'])}</b>")

    # — Топ-шаблоны —
    if d["top_templates"]:
        L.append("\n🔥 <b>Топ-шаблоны</b>")
        for i, t in enumerate(d["top_templates"], 1):
            mark = "🎞" if t.get("file_type") == "animation" else "🖼"
            title = (t["title"] or "без названия")[:28]
            L.append(f"  {i}. {mark} {title} — <b>{_fmt(t['use_count'])}</b>")

    # — Инлайн-аудитория —
    L.append(
        f"\n🎯 <b>Инлайн-аудитория</b>\n"
        f"В чатах: <b>{_fmt(inline.get('audience_chats', 0))}</b> уник / "
        f"<b>{_fmt(inline.get('audience_chats_total', 0))}</b> всего\n"
        f"   группы {_fmt(inline.get('audience_group', 0))} · "
        f"супергруппы {_fmt(inline.get('audience_supergroup', 0))} · "
        f"каналы {_fmt(inline.get('audience_channel', 0))}\n"
        f"📩 В ЛС: <b>{_fmt(inline.get('audience_dm', 0))}</b> уник / "
        f"<b>{_fmt(inline.get('audience_dm_total', 0))}</b> всего"
    )

    # — Реклама (сводка по всем ссылкам) —
    L.append(
        f"\n🔗 <b>Реклама</b>\n"
        f"Ссылок: <b>{_fmt(links['links_total'])}</b> (активных {_fmt(links['links_active'])})\n"
        f"Переходов: <b>{_fmt(links['clicks_total'])}</b> (сегодня {_fmt(links['clicks_today'])})  ·  "
        f"уник: <b>{_fmt(links['unique_total'])}</b>\n"
        f"👤 живые: <b>{_fmt(links['live_total'])}</b>  ·  "
        f"✅ прошли ОП: <b>{_fmt(links['op_passed_total'])}</b> "
        f"({_pct(links['op_passed_total'], links['unique_total'])}%)"
    )

    # — Размещения —
    L.append(f"\n🪧 <b>Размещения:</b> всего {_fmt(pl['total'])} · активных {_fmt(pl['active'])}")
    if pl.get("recent"):
        for p in pl["recent"]:
            is_running = p["is_active"] and p["starts_at"].replace(tzinfo=None) <= datetime.utcnow() <= p["ends_at"].replace(tzinfo=None)
            icon = "🟢" if is_running else ("🟡" if p["is_active"] else "🔴")
            L.append(f"  {icon} {(p['name'] or '')[:24]} — {_fmt(p.get('total_sends', 0))} отпр. / {_fmt(p.get('unique_users', 0))} юз.")

    # — Показы —
    if displays:
        L.append("\n📺 <b>Показы:</b>")
        for dd in displays:
            icon = "🟢" if dd["is_active"] else "🔴"
            L.append(f"  {icon} {(dd['name'] or '')[:24]} — {_fmt(dd.get('views', 0))} уник.")

    return "\n".join(L)


async def _gather_displays_with_views(pool) -> list[dict]:
    items = await get_all_displays(pool)
    out = []
    for d in items[:8]:
        d["views"] = await get_display_views_count(pool, d["id"])
        out.append(d)
    return out


async def _gather_all_stats(pool) -> dict:
    """Собираем весь дашборд статистики одним местом."""
    return {
        "users":         await get_users_count(pool),
        "growth":        await get_growth_stats(pool),
        "new_by_day":    await get_new_users_by_day(pool, 7),
        "gen":           await get_generation_stats(pool),
        "gen_by_day":    await get_generation_by_day(pool, 7),
        "top_gen_week":  await get_top_generators(pool, limit=5, days=7),
        "top_templates": await get_top_templates(pool, 5),
        "inline":        await get_inline_stats(pool),
        "placements":    await get_placements_stats(pool),
        "displays":      await _gather_displays_with_views(pool),
        "links":         await get_links_overview(pool),
    }


async def send_db_stats(message: Message):
    pool = get_pool()
    stats = await get_users_count(pool)
    await message.answer(_build_db_text(stats), parse_mode="HTML", reply_markup=database_menu())


async def send_placements_stats(message: Message):
    pool = get_pool()
    data = await _gather_all_stats(pool)
    await message.answer(
        _build_stats_text(data),
        parse_mode="HTML",
        reply_markup=stats_menu(),
    )


@router.callback_query(F.data == "db:refresh")
async def cb_db_refresh(callback: CallbackQuery):
    if not await can_db(callback.from_user.id):
        return
    pool = get_pool()
    stats = await get_users_count(pool)
    try:
        await callback.message.edit_text(
            _build_db_text(stats), parse_mode="HTML", reply_markup=database_menu()
        )
        await callback.answer("Обновлено")
    except TelegramBadRequest:
        await callback.answer("Уже актуально")


@router.callback_query(F.data == "stats:refresh")
async def cb_stats_refresh(callback: CallbackQuery):
    if not await can_stats(callback.from_user.id):
        return
    pool = get_pool()
    data = await _gather_all_stats(pool)
    try:
        await callback.message.edit_text(
            _build_stats_text(data),
            parse_mode="HTML",
            reply_markup=stats_menu(),
        )
        await callback.answer("Обновлено")
    except TelegramBadRequest:
        await callback.answer("Уже актуально")


@router.callback_query(F.data == "db:unblock_all")
async def cb_db_unblock_all(callback: CallbackQuery):
    if not await can_db(callback.from_user.id):
        return
    pool = get_pool()
    count = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = TRUE")
    await pool.execute("UPDATE users SET is_blocked = FALSE WHERE is_blocked = TRUE")
    stats = await get_users_count(pool)
    try:
        await callback.message.edit_text(_build_db_text(stats), parse_mode="HTML", reply_markup=database_menu())
    except TelegramBadRequest:
        pass
    await callback.answer(f"Разблокировано {count} пользователей", show_alert=True)


@router.callback_query(F.data == "db:export")
async def cb_db_export(callback: CallbackQuery):
    if not await can_db(callback.from_user.id):
        return
    await callback.answer("Генерирую CSV...")

    pool = get_pool()
    users = await get_users_export(pool)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "username", "first_name", "last_name", "gender",
                     "is_premium", "language_code", "is_blocked", "created_at", "last_active"])
    for u in users:
        writer.writerow([
            u["id"], u["username"] or "", u["first_name"] or "",
            u["last_name"] or "", u["gender"], u["is_premium"],
            u["language_code"] or "", u["is_blocked"],
            u["created_at"].strftime("%Y-%m-%d %H:%M:%S") if u["created_at"] else "",
            u["last_active"].strftime("%Y-%m-%d %H:%M:%S") if u["last_active"] else "",
        ])

    data = buf.getvalue().encode("utf-8-sig")
    file = BufferedInputFile(data, filename="users.csv")
    await callback.message.answer_document(file, caption=f"👥 База: {len(users)} пользователей")
