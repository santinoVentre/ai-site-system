#!/bin/bash
# =============================================================
# AI Site System — VPS deployment
# Run from the project root on the VPS (where docker-compose.yml lives).
# Assumes: git is already configured to pull from
#          https://github.com/santinoVentre/ai-site-system
# =============================================================
set -euo pipefail

if [ ! -f docker-compose.yml ]; then
  echo "ERROR: Run this script from the project root (docker-compose.yml not found)."
  exit 1
fi

echo "==> 1/6 Pulling latest code"
git fetch --all --prune
git reset --hard origin/main

echo "==> 2/6 Ensuring .env has new variables"
if [ ! -f .env ]; then
  echo "No .env found — copying from .env.example. EDIT IT and re-run."
  cp .env.example .env
  exit 1
fi

# Append missing keys with safe defaults so the compose file does not fail.
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
append_if_missing "SITE_BASE_URL" "https://${DOMAIN:-localhost}"
append_if_missing "CMS_ASSETS_PATH" "/data/cms-assets"
append_if_missing "MAX_UPLOAD_SIZE" "10485760"

echo "==> 3/6 Building changed service images"
docker compose build --pull agent-api admin-web qa-runner telegram-bot

echo "==> 4/6 Restarting services (recreate only if needed)"
docker compose up -d

echo "==> 5/6 Waiting for health checks"
for svc in agent-api admin-web qa-runner telegram-bot; do
  for i in $(seq 1 30); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "ai-site-${svc}" 2>/dev/null || echo "unknown")
    if [ "$status" = "healthy" ]; then
      echo "   ✓ ${svc} healthy"
      break
    fi
    sleep 2
  done
  if [ "$status" != "healthy" ]; then
    echo "   ! ${svc} not healthy after 60s (status=${status}) — check logs:"
    echo "     docker compose logs --tail=80 ${svc}"
  fi
done

echo "==> 6/6 Smoke tests"
SECRET=$(grep -E '^AGENT_API_SECRET=' .env | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
echo "   GET /health:"
docker exec ai-site-agent-api curl -sf http://127.0.0.1:8000/health || echo "   ! agent-api /health failed"
echo
echo "   GET /projects (auth):"
docker exec ai-site-agent-api curl -sf -H "X-API-Secret: ${SECRET}" http://127.0.0.1:8000/projects > /dev/null && echo "   ✓ /projects OK" || echo "   ! /projects failed"

echo
echo "Deploy complete. Dashboard: https://${DOMAIN:-<your-domain>}/admin/"
