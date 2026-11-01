#!/usr/bin/env bash
# Установка max-messenger-mcp на VPS (Debian/Ubuntu). Запуск из корня репозитория: sudo bash deploy/install.sh
# Свой пользователь, свой каталог, свой порт на 127.0.0.1 — другие сервисы на сервере не затрагиваются.
# Повторный запуск обновляет код и зависимости, настройки и сессия сохраняются.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/max-mcp
ENVF=/etc/max-mcp/env

apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl openssl

id maxmcp >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin maxmcp
mkdir -p "$APP" /etc/max-mcp /var/lib/max-mcp
cp "$ROOT"/app/*.py "$APP"/
chown -R root:root "$APP"
chown maxmcp:maxmcp /var/lib/max-mcp && chmod 700 /var/lib/max-mcp

[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q -U pip
"$APP/venv/bin/pip" install -q -r "$ROOT/requirements.txt"

if [ ! -f "$ENVF" ]; then
  cp "$ROOT/deploy/env.example" "$ENVF"
  echo "MAX_SECRET=$(openssl rand -hex 24)" >> "$ENVF"
fi
chown root:maxmcp "$ENVF" && chmod 640 "$ENVF"

cp "$ROOT/deploy/max-mcp.service" /etc/systemd/system/max-mcp.service
systemctl daemon-reload
systemctl is-active --quiet max-mcp && systemctl restart max-mcp

cat <<'EOF'

Дальше по порядку:
  1) sudo bash deploy/login.sh                 — вход в MAX по QR
  2) sudo systemctl enable --now max-mcp
  3) sudo bash deploy/setup-https.sh [домен]   — HTTPS-адрес для ChatGPT / Claude
Настройки (куда можно отправлять и т. п.): sudo nano /etc/max-mcp/env
EOF
