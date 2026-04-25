#!/usr/bin/env bash
# Custom CMS — end-to-end smoke test on the VPS.
# Validates: kinds registry, sections CRUD, items CRUD, public read endpoint,
# image asset directory writable, optional seeded section read.
set -euo pipefail
cd /opt/ai-site-system

SECRET=$(grep -E '^AGENT_API_SECRET=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
API="http://127.0.0.1:8000"

H_SECRET=( -H "X-API-Secret: ${SECRET}" )
DEX="docker exec -i ai-site-agent-api"

echo "==> 1/8 GET /cms/kinds (catalog)"
$DEX curl -sf "${H_SECRET[@]}" "${API}/cms/kinds" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   total kinds:", len(d)); [print("   -", k["kind"], "->", k["label"]) for k in d]'

echo
echo "==> 2/8 Pick first existing project (or create one if none)"
PROJECT_ID=$($DEX curl -sf "${H_SECRET[@]}" "${API}/projects" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); items=d["projects"] if isinstance(d, dict) else d; print(items[0]["id"] if items else "")')
PROJECT_SLUG=$($DEX curl -sf "${H_SECRET[@]}" "${API}/projects" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); items=d["projects"] if isinstance(d, dict) else d; print(items[0]["slug"] if items else "")')

if [ -z "$PROJECT_ID" ]; then
    echo "   no projects found — skipping per-project tests"
    exit 0
fi
echo "   using project: ${PROJECT_SLUG} (${PROJECT_ID})"

echo
echo "==> 3/8 GET /projects/{id}/cms/sections"
$DEX curl -sf "${H_SECRET[@]}" "${API}/projects/${PROJECT_ID}/cms/sections" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   sections:", len(d)); [print("   -", s["kind"], s["key"], "items=", s.get("item_count")) for s in d]'

echo
echo "==> 4/8 POST /projects/{id}/cms/sections (smoke FAQ)"
TEST_KEY="smoke-faq-$(date +%s)"
SECTION_JSON=$(cat <<JSON
{"kind":"faq","key":"${TEST_KEY}","label":"Smoke FAQ","seed_examples":true}
JSON
)
SECTION_ID=$(echo "$SECTION_JSON" | $DEX curl -sf "${H_SECRET[@]}" -H "Content-Type: application/json" \
    -X POST --data-binary @- "${API}/projects/${PROJECT_ID}/cms/sections" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "   created section id=${SECTION_ID} key=${TEST_KEY}"

echo
echo "==> 5/8 GET /cms/sections/{sid}/items (seeded)"
$DEX curl -sf "${H_SECRET[@]}" "${API}/cms/sections/${SECTION_ID}/items" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   items:", len(d)); [print("   -", i["data"].get("question","?")[:60]) for i in d]'

echo
echo "==> 6/8 POST a new item to the section"
ITEM_JSON='{"data":{"question":"E2E smoke test?","answer":"OK"}}'
ITEM_ID=$(echo "$ITEM_JSON" | $DEX curl -sf "${H_SECRET[@]}" -H "Content-Type: application/json" \
    -X POST --data-binary @- "${API}/cms/sections/${SECTION_ID}/items" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "   created item id=${ITEM_ID}"

echo
echo "==> 7/8 GET /projects/{slug}/cms/data (public hydration endpoint)"
docker exec ai-site-agent-api curl -sf "${API}/projects/${PROJECT_SLUG}/cms/data" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   sections in payload:", len(d)); [print("   -", k, "->", v.get("kind"), "items=", len(v.get("items", []))) for k,v in d.items()]'

echo
echo "==> 8/8 Cleanup smoke section + verify cms-assets dir is writable"
$DEX curl -sf "${H_SECRET[@]}" -X DELETE "${API}/cms/sections/${SECTION_ID}" -o /dev/null && echo "   deleted section ${SECTION_ID}"
$DEX sh -c 'mkdir -p /data/cms-assets && touch /data/cms-assets/.smoke && rm /data/cms-assets/.smoke && echo "   cms-assets writable"'
$DEX sh -c 'ls -ld /data/cms-assets'
echo
echo "================================================================"
echo "CMS SMOKE TEST: PASSED"
echo "================================================================"
