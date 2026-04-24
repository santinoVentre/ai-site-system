#!/bin/bash
# Rebuild agent-api, restart, reload nginx and optionally trigger retry on a job.
# Usage: ./rebuild_and_retry.sh [job_id]
set -euo pipefail

cd "${PROJECT_DIR:-/opt/ai-site-system}"

echo "=== Rebuilding agent-api ==="
docker compose build agent-api

echo "=== Restarting agent-api ==="
docker compose up -d agent-api

echo "=== Waiting 5s for startup ==="
sleep 5

echo "=== Reloading nginx ==="
docker exec ai-site-nginx nginx -s reload

JOB_ID="${1:-}"
if [ -z "$JOB_ID" ]; then
  echo "No job id provided — skipping retry."
  echo "Run: $0 <job_id>  to also trigger a retry."
  exit 0
fi

if [ ! -f .env ]; then
  echo "ERROR: .env not found in $(pwd)."
  exit 1
fi

SECRET=$(grep -E '^AGENT_API_SECRET=' .env | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
if [ -z "$SECRET" ]; then
  echo "ERROR: AGENT_API_SECRET not set in .env"
  exit 1
fi

echo "=== Triggering retry for job $JOB_ID ==="
docker exec ai-site-agent-api curl -s -X POST \
  "http://127.0.0.1:8000/jobs/${JOB_ID}/retry" \
  -H "X-API-Secret: ${SECRET}" \
  -H "Content-Type: application/json"

echo ""
echo "=== Waiting 60s then checking status ==="
sleep 60

docker exec ai-site-postgres psql -U postgres -d ai_site_system \
  -c "SELECT status, LEFT(error_message,200) as error FROM jobs WHERE id='${JOB_ID}';"

echo ""
echo "=== Last 30 agent-api logs ==="
docker logs --tail=30 ai-site-agent-api
