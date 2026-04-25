#!/usr/bin/env bash
set -euo pipefail
cd /opt/ai-site-system

echo "==> Build images (agent-api, admin-web, qa-runner, telegram-bot)"
docker compose build --pull agent-api admin-web qa-runner telegram-bot

echo
echo "==> Apply: docker compose up -d"
docker compose up -d

echo
echo "==> Wait for health checks"
for svc in agent-api admin-web qa-runner telegram-bot; do
    status="unknown"
    for i in $(seq 1 90); do
        status=$(docker inspect -f '{{.State.Health.Status}}' "ai-site-${svc}" 2>/dev/null || echo "missing")
        if [ "$status" = "healthy" ]; then
            echo "   OK ${svc} healthy"
            break
        fi
        sleep 2
    done
    if [ "$status" != "healthy" ]; then
        echo "   !! ${svc} not healthy after 180s (last status=${status})"
        echo "   --- last 60 log lines: ---"
        docker compose logs --tail=60 "${svc}" || true
        echo "   --------------------------"
    fi
done

echo
echo "==> Smoke tests"
SECRET=$(grep -E '^AGENT_API_SECRET=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
DOMAIN_VAL=$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
echo "   GET agent-api /health:"
docker exec ai-site-agent-api curl -sf http://127.0.0.1:8000/health && echo "   OK" || echo "   !! /health failed"
echo
echo "   GET agent-api /projects (auth):"
docker exec ai-site-agent-api curl -sf -H "X-API-Secret: ${SECRET}" http://127.0.0.1:8000/projects > /dev/null && echo "   OK" || echo "   !! /projects failed"
echo
echo "   GET admin-web /health:"
docker exec ai-site-admin-web curl -sf http://127.0.0.1:8002/health && echo "   OK" || echo "   !! admin /health failed"
echo
echo "   GET qa-runner /health:"
docker exec ai-site-qa-runner curl -sf http://127.0.0.1:8001/health && echo "   OK" || echo "   !! qa /health failed"
echo
echo "   GET telegram-bot /health:"
docker exec ai-site-telegram-bot curl -sf http://127.0.0.1:8080/health && echo "   OK" || echo "   !! telegram /health failed"
echo
echo "   GET nginx -> /health:"
curl -sfk "https://${DOMAIN_VAL:-localhost}/health" && echo "   OK" || echo "   !! nginx https /health failed"

echo
echo "================================================================"
echo "BUILD/RESTART/SMOKE COMPLETE"
echo "================================================================"
