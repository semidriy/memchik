#!/bin/bash
# Запускать НА СЕРВЕРЕ после того, как новый код уже залит в /opt/memchik
# (git pull / scp / rsync). Делает всё, без чего клиент НЕ увидит изменения:
#   1) ставит шрифты с глифом «?»  2) накатывает миграции БД
#   3) сбрасывает кэш отрисованных гифок  4) перезапускает сервисы.
#
# Использование:  sudo bash /opt/memchik/deploy/update.sh
set -e

PROJECT=/opt/memchik
cd "$PROJECT"

# .env содержит DATABASE_URL (нужен для миграций и сброса кэша)
set -a; source "$PROJECT/.env"; set +a

echo "==> 1/4  Шрифты (иначе «?» и часть пунктуации не рисуются)"
apt-get update -qq && apt-get install -y fonts-dejavu-core fonts-dejavu fonts-noto-core || true

echo "==> 2/4  Миграции БД (ADD COLUMN IF NOT EXISTS / новые таблицы — идемпотентно)"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$PROJECT/migrations/schema.sql"

echo "==> 3/4  Сброс кэша отрисованных гифок"
# КРИТИЧНО: уже отрисованные пары (шаблон, текст) кэшируются навсегда по text_hash —
# и в БД (gif_cache → вечный file_id), и на диске (media_cache/proc), и в памяти media_server.
# Без сброса старые гифки со сломанным «?»/старой отрисовкой будут отдаваться вечно,
# и фиксы «не видно». Чистим — гифки перегенерятся при следующем запросе.
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "TRUNCATE gif_cache;"
rm -f "$PROJECT"/media_cache/proc/*   "$PROJECT"/media_cache/thumb/*  2>/dev/null || true
# (media_cache/raw — это скачанные исходники шаблонов, их трогать не нужно)

echo "==> 4/4  Перезапуск сервисов (сбрасывает и in-memory LRU media_server)"
systemctl restart media_server bot
systemctl --no-pager status media_server bot | head -n 20 || true

VER=$(grep -oP 'BOT_VERSION\s*=\s*"\K[^"]+' "$PROJECT/bot/version.py" 2>/dev/null || echo "?")
echo "Готово. Версия на сервере: $VER  (проверь в боте: /ver)"
echo "Проверка media-сервера: curl -s https://tgp.tgis.vu/health"
