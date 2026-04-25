#!/usr/bin/env bash
# Executed on the VPS via: ssh root@... bash -s < scripts/remote-deploy.sh
set -euo pipefail

echo "================================================================"
echo "AI Site System — VPS deploy ($(date -u +'%Y-%m-%dT%H:%M:%SZ'))"
echo "================================================================"

cd /opt/ai-site-system

TS=$(date +%s)
echo
echo "==> 1/8 Backup .env + secrets/ (timestamp ${TS})"
cp .env "/tmp/aisite.env.${TS}.bak"
cp -r secrets "/tmp/aisite.secrets.${TS}.bak"
echo "   .env       -> /tmp/aisite.env.${TS}.bak"
echo "   secrets/   -> /tmp/aisite.secrets.${TS}.bak"

echo
echo "==> 2/8 Sync repo with origin/main"
git fetch --all --prune
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$CURRENT_BRANCH" != "main" ]; then
    # Switch to main without losing any uncommitted .env/secrets work (they're gitignored)
    git checkout -B main origin/main
fi
# Hard-reset to origin/main — drops any local commits/changes (.env + secrets are gitignored)
git reset --hard origin/main
git branch -d master 2>/dev/null || true
# Defensive: if .env or secrets got removed somehow (they are gitignored, but just in case)
[ -f .env ] || cp "/tmp/aisite.env.${TS}.bak" .env
[ -d secrets ] || cp -r "/tmp/aisite.secrets.${TS}.bak" secrets
echo "   On commit: $(git log -1 --oneline)"

echo
echo "==> 3/8 Append missing .env keys with safe defaults"
append_if_missing() {
    local key="$1"; local default="$2"
    if ! grep -qE "^${key}=" .env; then
        echo "${key}=${default}" >> .env
        echo "   + ${key}=${default}"
    fi
}
append_if_missing "UNSPLASH_ACCESS_KEY" ""
append_if_missing "PEXELS_API_KEY" ""
append_if_missing "REPLICATE_API_TOKEN" ""
append_if_missing "AI_IMAGES_ENABLED" "false"
append_if_missing "QUALITY_SCORE_THRESHOLD" "80"
append_if_missing "QUALITY_MAX_ITERATIONS" "2"
append_if_missing "QA_RUNNER_URL" "http://qa-runner:8001"
append_if_missing "QA_ENABLED" "true"
append_if_missing "LOG_LEVEL" "INFO"
append_if_missing "LOG_JSON" "true"
append_if_missing "RATE_LIMIT_DEFAULT" "60/minute"
append_if_missing "RATE_LIMIT_PUBLIC" "60/minute"
DOMAIN_VAL=$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
append_if_missing "SITE_BASE_URL" "https://${DOMAIN_VAL:-localhost}"
append_if_missing "CMS_ASSETS_PATH" "/data/cms-assets"
append_if_missing "MAX_UPLOAD_SIZE" "10485760"
append_if_missing "CORS_ALLOWED_ORIGINS" "https://${DOMAIN_VAL:-localhost}"

echo
echo "==> 4/8 Pre-create data directories (idempotent)"
mkdir -p data/generated-sites data/artifacts data/backups data/cms-assets data/certbot/www data/certbot/conf nginx/ssl
# Container runs as appuser uid:gid = 999:999 (see services/agent-api/Dockerfile).
# Bind-mounted CMS asset dir must be writable by that uid so image uploads work.
chown -R 999:999 data/cms-assets
chmod 755 data/cms-assets
echo "   ok"

echo
echo "==> 5/8 Build updated service images (this can take a few minutes)"
docker compose build --pull agent-api admin-web qa-runner telegram-bot

echo
echo "==> 6/8 Apply changes (docker compose up -d)"
docker compose up -d

echo
echo "==> 7/8 Wait for health checks"
for svc in agent-api admin-web qa-runner telegram-bot; do
    status="unknown"
    for i in $(seq 1 60); do
        status=$(docker inspect -f '{{.State.Health.Status}}' "ai-site-${svc}" 2>/dev/null || echo "missing")
        if [ "$status" = "healthy" ]; then
            echo "   OK ${svc} healthy"
            break
        fi
        sleep 2
    done
    if [ "$status" != "healthy" ]; then
        echo "   !! ${svc} not healthy after 120s (last status=${status})"
        echo "   --- last 40 log lines: ---"
        docker compose logs --tail=40 "${svc}" || true
        echo "   --------------------------"
    fi
done

echo
echo "==> 8/8 Smoke tests"
SECRET=$(grep -E '^AGENT_API_SECRET=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
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
echo "================================================================"
echo "DEPLOY COMPLETE"
echo "Backups:"
echo "  .env     -> /tmp/aisite.env.${TS}.bak"
echo "  secrets/ -> /tmp/aisite.secrets.${TS}.bak"
echo "Dashboard: https://${DOMAIN_VAL:-<your-domain>}/admin/"
echo "================================================================"
