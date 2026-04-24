#!/bin/bash
# Run retry for a job from inside the agent-api container.
# Usage: ./retry_job_curl.sh <job_id>
# Secret is read from .env — never hardcode it here.
set -euo pipefail

JOB_ID="${1:-}"
if [ -z "$JOB_ID" ]; then
  echo "Usage: $0 <job_id>"
  exit 1
fi

if [ ! -f .env ]; then
  echo "ERROR: .env not found in $(pwd). Run this from the project root."
  exit 1
fi

SECRET=$(grep -E '^AGENT_API_SECRET=' .env | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
if [ -z "$SECRET" ]; then
  echo "ERROR: AGENT_API_SECRET not set in .env"
  exit 1
fi

docker exec ai-site-agent-api curl -s -X POST \
  "http://127.0.0.1:8000/jobs/${JOB_ID}/retry" \
  -H "X-API-Secret: ${SECRET}" \
  -H "Content-Type: application/json"

echo ""
echo "Done."
