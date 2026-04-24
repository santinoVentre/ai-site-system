#!/bin/bash
cd /opt/ai-site-system
SECRET=$(grep AGENT_API_SECRET .env | cut -d= -f2 | tr -d '"' | tr -d "'" | tr -d ' ' | tr -d '\r')
echo "SECRET length: ${#SECRET}"
echo ""
echo "=== Testing curl to retry endpoint ==="
curl -v -X POST "http://localhost:8000/jobs/28b21f66-41cb-4c2e-ba37-e61f559761b8/retry" \
  -H "X-API-Secret: $SECRET" \
  -H "Content-Type: application/json" \
  2>&1
echo ""
echo "=== Exit code: $? ==="
