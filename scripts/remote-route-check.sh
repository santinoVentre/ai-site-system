#!/usr/bin/env bash
set -e

echo "--- admin-web internal route discovery ---"
# Ping each known admin-web path
for p in / /login /logout /static/ /admin /admin/ /admin/login /health /openapi.json /docs ; do
    code=$(docker exec ai-site-admin-web curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:8002${p}" || echo "ERR")
    echo "  ${p}  -> ${code}"
done

echo
echo "--- agent-api internal openapi.json (paths) ---"
docker exec ai-site-agent-api curl -sS http://127.0.0.1:8000/openapi.json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(p) for p in sorted(d.get("paths", {}).keys())]' \
  || echo "no openapi"

echo
echo "--- agent-api internal route ping ---"
for p in /health /projects /qa/health /openapi.json ; do
    code=$(docker exec ai-site-agent-api curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:8000${p}" || echo "ERR")
    echo "  ${p}  -> ${code}"
done

echo
echo "--- public HTTPS via nginx ---"
for p in / /login /admin /admin/ /admin/login /api/health /api/projects /health ; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" "https://agent.santinoventre.com${p}" || echo "ERR")
    echo "  ${p}  -> ${code}"
done
