"""Permission checks for admin access. Super admins from settings.admin_ids have all permissions.
Dynamic admins (table bot_admins) have whatever permissions are stored in their row."""

from bot.config import settings
from bot.database import get_pool
from bot.database.queries import get_admin_permissions

PERM_BROADCAST = "broadcast"
PERM_OP = "op"
PERM_LINKS = "links"
PERM_TEMPLATES = "templates"
PERM_STATS = "stats"
PERM_DATABASE = "database"
PERM_MESSAGES = "messages"
PERM_PLACEMENTS = "placements"
PERM_DISPLAYS = "displays"
PERM_MODERATION = "moderation"
PERM_MANAGE_ADMINS = "manage_admins"
PERM_LANGUAGES = "languages"

ALL_PERMISSIONS: list[str] = [
    PERM_BROADCAST, PERM_OP, PERM_LINKS, PERM_TEMPLATES,
    PERM_STATS, PERM_DATABASE, PERM_MESSAGES, PERM_PLACEMENTS,
    PERM_DISPLAYS, PERM_MODERATION, PERM_MANAGE_ADMINS, PERM_LANGUAGES,
]

PERM_LABELS: dict[str, str] = {
    PERM_BROADCAST: "📨 Рассылка",
    PERM_OP: "🔐 ОП каналы",
    PERM_LINKS: "🔗 Ссылки",
    PERM_TEMPLATES: "🎭 Шаблоны",
    PERM_STATS: "📊 Статистика",
    PERM_DATABASE: "👥 База данных",
    PERM_MESSAGES: "✉️ Сообщения",
    PERM_PLACEMENTS: "🪧 Размещения",
    PERM_DISPLAYS: "📺 Показы",
    PERM_MODERATION: "🛡 Модерация",
    PERM_MANAGE_ADMINS: "👮 Управление админами",
    PERM_LANGUAGES: "🌐 Языки",
}


def is_super_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


async def has_permission(user_id: int, perm: str) -> bool:
    if is_super_admin(user_id):
        return True
    perms = await get_admin_permissions(get_pool(), user_id)
    return perms is not None and perm in perms


async def is_admin(user_id: int) -> bool:
    """True if user has any admin access at all."""
    if is_super_admin(user_id):
        return True
    perms = await get_admin_permissions(get_pool(), user_id)
    return perms is not None and len(perms) > 0


async def get_user_permissions(user_id: int) -> list[str]:
    if is_super_admin(user_id):
        return list(ALL_PERMISSIONS)
    perms = await get_admin_permissions(get_pool(), user_id)
    return list(perms or [])
