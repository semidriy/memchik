#!/bin/bash
# Run once on the server to set everything up
set -e

PROJECT=/opt/memchik

# 0. Fonts for the text overlay (DejaVu/Noto cover Cyrillic + punctuation incl. "?").
# Без них media_server подбирает случайный системный шрифт, где может не быть глифа "?".
apt-get update && apt-get install -y fonts-dejavu-core fonts-dejavu fonts-noto-core || true

# 1. SSL certificate
certbot certonly --nginx -d tgp.tgis.vu

# 2. Nginx cache dir
mkdir -p /var/cache/nginx/tgp
chown www-data:www-data /var/cache/nginx/tgp

# 3. Nginx config
cp $PROJECT/deploy/nginx_tgp.conf /etc/nginx/sites-available/tgp.tgis.vu
ln -sf /etc/nginx/sites-available/tgp.tgis.vu /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 4. Media cache dir
mkdir -p $PROJECT/media_cache/raw $PROJECT/media_cache/proc $PROJECT/media_cache/thumb

# 5. Systemd services
cp $PROJECT/deploy/media_server.service /etc/systemd/system/
cp $PROJECT/deploy/bot.service          /etc/systemd/system/
systemctl daemon-reload
systemctl enable media_server bot
systemctl restart media_server bot

echo "Done. Check: curl https://tgp.tgis.vu/health"
