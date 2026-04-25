#!/usr/bin/env bash
# Verify the full image upload pipeline:
#  - POST a real PNG to /projects/{id}/cms/images
#  - confirm the file lands under /data/cms-assets/{project_id}/...
#  - confirm nginx serves it on /cms-assets/{project_id}/{file}
set -euo pipefail
cd /opt/ai-site-system
SECRET=$(grep -E '^AGENT_API_SECRET=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
DOMAIN=$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d ' ')
PID="7099556a-dc31-4eab-ad30-caf7fe7b67b5"

echo "==> 1/5 Generate a tiny valid PNG locally"
python3 -c "
import zlib, struct, base64, sys
def png(w=64, h=64, color=(15, 142, 30)):
    sig = b'\x89PNG\r\n\x1a\n'
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes(color * w) for _ in range(h))
    idat = zlib.compress(raw)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')
open('/tmp/smoke.png', 'wb').write(png())
print('   wrote /tmp/smoke.png', len(open('/tmp/smoke.png','rb').read()), 'bytes')
"

echo
echo "==> 2/5 Copy PNG into agent-api container"
docker cp /tmp/smoke.png ai-site-agent-api:/tmp/smoke.png

echo
echo "==> 3/5 POST multipart upload to /projects/{id}/cms/images"
RESP=$(docker exec ai-site-agent-api curl -sf \
    -H "X-API-Secret: ${SECRET}" \
    -F "file=@/tmp/smoke.png;type=image/png" \
    "http://127.0.0.1:8000/projects/${PID}/cms/images")
echo "   response: ${RESP}"
URL=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url",""))')
echo "   relative url: ${URL}"

echo
echo "==> 4/5 Verify file is on host disk"
DIR="$(dirname "${URL#/cms-assets/}")"
FN="$(basename "${URL}")"
ls -la "data/cms-assets/${DIR}/" | head -5

echo
echo "==> 5/5 Verify nginx serves it (HTTPS public)"
PUBLIC_URL="https://${DOMAIN}${URL}"
echo "   GET ${PUBLIC_URL}"
HTTP=$(curl -sk -o /dev/null -w '%{http_code} %{content_type} %{size_download}' "${PUBLIC_URL}")
echo "   http_code/content_type/size: ${HTTP}"

echo
echo "==> Cleanup uploaded file"
rm -f "data/cms-assets/${DIR}/${FN}"
docker exec -u root ai-site-agent-api rm -f /tmp/smoke.png || true
rm -f /tmp/smoke.png

echo
echo "================================================================"
echo "IMAGE UPLOAD SMOKE: DONE"
echo "================================================================"
