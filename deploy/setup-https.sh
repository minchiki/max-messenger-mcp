#!/usr/bin/env bash
# HTTPS-адрес для ChatGPT через nginx + бесплатный сертификат Let's Encrypt.
# Запуск: sudo bash deploy/setup-https.sh [домен]   (без аргумента берётся <ip>.sslip.io)
# Если для этого имени уже есть сайт nginx (например, другой MCP), добавьте в него только блок location ниже.
# Нужны открытые порты 80 и 443.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }
set -a; . /etc/max-mcp/env; set +a

HOST="${1:-}"
if [ -z "$HOST" ]; then
  IP="$(curl -fsS -m 10 https://api.ipify.org || hostname -I | awk '{print $1}')"
  HOST="${IP//./-}.sslip.io"
fi

command -v nginx >/dev/null || apt-get install -y nginx
apt-get install -y certbot python3-certbot-nginx

# многопользовательский режим: наружу весь сайт (главная, вход, OAuth, /mcp); иначе — только секретный путь
if [ "${MAX_MULTIUSER:-0}" = 1 ]; then LOC=/; ELSE_404=""; URL="https://$HOST/mcp"
else LOC="/$MAX_SECRET/mcp"; ELSE_404="location / { return 404; }"; URL="https://$HOST/$MAX_SECRET/mcp"; fi

if grep -rqs "server_name $HOST;" /etc/nginx/sites-enabled/ && ! [ -f /etc/nginx/sites-enabled/max-mcp ]; then
  echo "Для $HOST уже есть сайт nginx. Добавьте в его server { listen 443 ... } блок:"
  echo "    location $LOC { proxy_pass http://127.0.0.1:${MAX_PORT:-8766}; proxy_http_version 1.1; proxy_set_header Host \$host; proxy_buffering off; proxy_read_timeout 300s; }"
  echo "затем: sudo nginx -t && sudo systemctl reload nginx"
  echo "Адрес: $URL"
  exit 0
fi

PROXY="proxy_pass http://127.0.0.1:${MAX_PORT:-8766}; proxy_http_version 1.1; proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for; proxy_buffering off; proxy_read_timeout 300s;"

if [ "${MAX_MULTIUSER:-0}" = 1 ]; then
  # лимиты по IP клиента (за ретранслятором — первый адрес из X-Forwarded-For):
  # вход и регистрация — 30 в минуту, остальное — 600 в минуту
  cat > /etc/nginx/conf.d/max-mcp-limits.conf <<'EOF'
map $http_x_forwarded_for $max_client { "~^(?<ip>[^,\s]+)" $ip; default $binary_remote_addr; }
limit_req_zone $max_client zone=max_auth:10m rate=30r/m;
limit_req_zone $max_client zone=max_api:10m rate=600r/m;
limit_req_status 429;
EOF
  LOCATIONS="client_max_body_size 2m;
    location ~ ^/(register|authorize|token|revoke)\$ { limit_req zone=max_auth burst=20 nodelay; $PROXY }
    location ~ ^/login/[^/]+/(start|password)\$ { limit_req zone=max_auth burst=20 nodelay; $PROXY }
    location / { limit_req zone=max_api burst=200 nodelay; $PROXY }"
else
  LOCATIONS="location $LOC { $PROXY }
    $ELSE_404"
fi

cat > /etc/nginx/sites-available/max-mcp <<EOF
server {
    listen 80;
    server_name $HOST;
    $LOCATIONS
}
EOF
ln -sf /etc/nginx/sites-available/max-mcp /etc/nginx/sites-enabled/max-mcp
nginx -t
systemctl reload nginx

certbot --nginx -d "$HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect
nginx -t && systemctl reload nginx

echo
if [ "${MAX_MULTIUSER:-0}" = 1 ]; then
  echo "Сервис: https://$HOST  — адрес для ChatGPT и Claude: $URL"
  [ "${MAX_PUBLIC_URL:-}" = "https://$HOST" ] || echo "Проверьте MAX_PUBLIC_URL в /etc/max-mcp/env: сейчас '${MAX_PUBLIC_URL:-}', нужен https://$HOST (или адрес ретранслятора)"
else
  echo "Адрес для ChatGPT (держите в секрете):"
  echo "$URL"
fi
