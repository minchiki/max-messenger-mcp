#!/usr/bin/env bash
# Одноразовый вход в MAX по QR на сервере. Запуск: sudo bash deploy/login.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }
set -a; . /etc/max-mcp/env; set +a
systemctl stop max-mcp 2>/dev/null || true
cd /opt/max-mcp
runuser -u maxmcp -- /opt/max-mcp/venv/bin/python /opt/max-mcp/login.py
systemctl is-enabled --quiet max-mcp 2>/dev/null && systemctl start max-mcp || true
