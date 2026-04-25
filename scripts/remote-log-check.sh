#!/usr/bin/env bash
for svc in agent-api admin-web qa-runner telegram-bot; do
    echo "===== ai-site-${svc} (last 30 lines) ====="
    docker logs --tail 30 "ai-site-${svc}" 2>&1 || true
    echo
done
echo
echo "===== ERROR/WARNING grep across the 4 services (last 200) ====="
for svc in agent-api admin-web qa-runner telegram-bot; do
    out=$(docker logs --tail 200 "ai-site-${svc}" 2>&1 | grep -iE "error|warning|exception|traceback" | grep -vE "WARNING: Running pip|WARNING: There are .* slow") || true
    if [ -n "$out" ]; then
        echo "----- ${svc}:"
        echo "$out"
    fi
done
