#!/bin/bash
echo "=== JOB STATUS ==="
docker exec ai-site-postgres psql -U postgres -d ai_site_system -t -c "SELECT '  id: ' || id || chr(10) || '  status: ' || status || chr(10) || '  error: ' || COALESCE(error_message,'none') FROM jobs WHERE id='28b21f66-41cb-4c2e-ba37-e61f559761b8';"

echo ""
echo "=== LAST 50 AGENT-API LOGS ==="
cd /opt/ai-site-system && docker compose logs --tail 50 agent-api 2>&1
