#!/usr/bin/env bash
# Резервная копия базы сервиса (OAuth-клиенты, пользователи, зашифрованные сессии MAX).
# Запуск: sudo bash deploy/backup.sh [каталог]   (по умолчанию /var/backups/max-mcp, хранится 14 копий)
# Сессии в копии зашифрованы MAX_MASTER_KEY — храните ключ ОТДЕЛЬНО от копий, иначе копия = доступ к аккаунтам.
# Для ежедневной копии: echo "17 4 * * * root bash $(pwd)/deploy/backup.sh" | sudo tee /etc/cron.d/max-mcp-backup
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }
set -a; . /etc/max-mcp/env; set +a

DB="${MAX_DB:-${MAX_DIR:-/var/lib/max-mcp}/service.db}"
DEST="${1:-/var/backups/max-mcp}"
[ -f "$DB" ] || { echo "Базы $DB нет (сервис в режиме одного аккаунта или ещё не запускался)"; exit 0; }

install -d -m 700 "$DEST"
OUT="$DEST/service-$(date +%Y%m%d-%H%M%S).db"
# .backup делает согласованную копию даже во время работы сервиса
runuser -u maxmcp -- /opt/max-mcp/venv/bin/python - "$DB" "$OUT.tmp" <<'EOF'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
EOF
mv "$OUT.tmp" "$OUT" && chmod 600 "$OUT" && chown root:root "$OUT"
ls -1t "$DEST"/service-*.db | tail -n +15 | xargs -r rm -f
echo "Копия: $OUT"
