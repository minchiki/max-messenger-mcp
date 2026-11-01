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

if grep -rqs "server_name $HOST;" /etc/nginx/sites-enabled/ && ! [ -f /etc/nginx/sites-enabled/max-mcp ]; then
  echo "Для $HOST уже есть сайт nginx. Добавьте в его server { listen 443 ... } блок:"
  echo "    location /$MAX_SECRET/mcp { proxy_pass http://127.0.0.1:${MAX_PORT:-8766}; proxy_http_version 1.1; proxy_buffering off; proxy_read_timeout 300s; }"
  echo "затем: sudo nginx -t && sudo systemctl reload nginx"
  echo "Адрес: https://$HOST/$MAX_SECRET/mcp"
  exit 0
fi

cat > /etc/nginx/sites-available/max-mcp <<EOF
server {
    listen 80;
    server_name $HOST;
    location /$MAX_SECRET/mcp {
        proxy_pass http://127.0.0.1:${MAX_PORT:-8766};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
    location / { return 404; }
}
EOF
ln -sf /etc/nginx/sites-available/max-mcp /etc/nginx/sites-enabled/max-mcp
nginx -t
systemctl reload nginx

certbot --nginx -d "$HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect
nginx -t && systemctl reload nginx

echo
echo "Адрес для ChatGPT (держите в секрете):"
echo "https://$HOST/$MAX_SECRET/mcp"
