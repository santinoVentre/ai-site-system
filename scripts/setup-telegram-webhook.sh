#!/bin/bash
# =============================================================
#  AI Site System — Setup Telegram Webhook
#  Run after the system is up and domain is configured
# =============================================================
set -euo pipefail

INSTALL_DIR="/opt/ai-site-system"
source "$INSTALL_DIR/.env"

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ "$TELEGRAM_BOT_TOKEN" = "CHANGE_ME_telegram_bot_token" ]; then
    echo "Error: TELEGRAM_BOT_TOKEN not set in .env"
    exit 1
fi

WEBHOOK_URL="${PROTOCOL}://${DOMAIN}/webhook/telegram"

echo "Setting Telegram webhook to: $WEBHOOK_URL"

curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    -d "url=$WEBHOOK_URL" \
    -d "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
    -d "allowed_updates=[\"message\",\"callback_query\"]" | jq .

echo ""
echo "Verify webhook:"
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" | jq .
