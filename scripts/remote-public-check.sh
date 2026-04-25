#!/usr/bin/env bash
set -e
cd /opt/ai-site-system

DOMAIN_VAL=$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
PROTO_VAL=$(grep -E '^PROTOCOL=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
echo "DOMAIN=${DOMAIN_VAL}"
echo "PROTOCOL=${PROTO_VAL}"

echo
echo "--- public DNS resolution ---"
getent hosts "${DOMAIN_VAL}" || echo "no DNS resolution"

echo
echo "--- nginx published ports ---"
docker port ai-site-nginx

echo
echo "--- nginx server_name + locations ---"
docker exec ai-site-nginx sh -c 'cat /etc/nginx/conf.d/default.conf' | head -120

echo
echo "--- public HTTPS tests ---"
for path in "/" "/admin/" "/admin/login" "/api/health" "/n8n/" ; do
    code=$(curl -sS -k -o /dev/null -w "%{http_code}" "https://${DOMAIN_VAL}${path}" 2>/dev/null || echo "ERR")
    echo "https://${DOMAIN_VAL}${path}  -> ${code}"
done

echo
echo "--- public HTTP tests ---"
for path in "/" "/admin/" "/api/health" ; do
    code=$(curl -sS -o /dev/null -w "%{http_code}" "http://${DOMAIN_VAL}${path}" 2>/dev/null || echo "ERR")
    echo "http://${DOMAIN_VAL}${path}   -> ${code}"
done
