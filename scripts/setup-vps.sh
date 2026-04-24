#!/bin/bash
# =============================================================
#  AI Site System — VPS Setup Script
#  Target: Ubuntu 24.04 fresh VPS
#  Run as root or with sudo
# =============================================================
set -euo pipefail

INSTALL_DIR="/opt/ai-site-system"

echo "==========================================="
echo "  AI Site System — VPS Setup"
echo "==========================================="

# 1. System updates
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# 2. Install Docker
echo "[2/8] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
else
    echo "  Docker already installed."
fi

# 3. Install Docker Compose plugin
echo "[3/8] Verifying Docker Compose..."
if ! docker compose version &> /dev/null; then
    apt-get install -y docker-compose-plugin
fi
docker compose version

# 4. Install useful tools
echo "[4/8] Installing utilities..."
apt-get install -y -qq git curl wget htop unzip jq certbot

# 5. Create directory structure
echo "[5/8] Setting up directory structure..."
mkdir -p "$INSTALL_DIR"/{data/{generated-sites,artifacts,backups,certbot/{www,conf}},nginx/ssl}
chmod o+rx "$INSTALL_DIR/data/generated-sites"

# 6. Copy project files (if not already in place)
if [ "$PWD" != "$INSTALL_DIR" ]; then
    echo "  Copying project files to $INSTALL_DIR..."
    cp -r . "$INSTALL_DIR/" 2>/dev/null || echo "  Run this from the project directory."
fi

# 7. Setup .env
echo "[6/8] Setting up environment..."
cd "$INSTALL_DIR"
if [ ! -f .env ]; then
    cp .env.example .env

    # Generate secrets
    N8N_KEY=$(openssl rand -base64 32)
    ADMIN_KEY=$(openssl rand -hex 32)
    API_SECRET=$(openssl rand -hex 32)
    WEBHOOK_SECRET=$(openssl rand -hex 16)
    PG_PASS=$(openssl rand -base64 24 | tr -d '=/+')
    APP_PASS=$(openssl rand -base64 24 | tr -d '=/+')
    N8N_PASS=$(openssl rand -base64 24 | tr -d '=/+')
    REDIS_PASS=$(openssl rand -base64 24 | tr -d '=/+')
    ADMIN_PASS=$(openssl rand -base64 16 | tr -d '=/+')
    N8N_ADMIN_PASS=$(openssl rand -base64 16 | tr -d '=/+')

    sed -i "s|CHANGE_ME_pg_root_password|$PG_PASS|g" .env
    sed -i "s|CHANGE_ME_app_db_password|$APP_PASS|g" .env
    sed -i "s|CHANGE_ME_n8n_db_password|$N8N_PASS|g" .env
    sed -i "s|CHANGE_ME_redis_password|$REDIS_PASS|g" .env
    sed -i "s|CHANGE_ME_generate_with_openssl_rand_base64_32|$N8N_KEY|g" .env
    sed -i "s|CHANGE_ME_n8n_admin_password|$N8N_ADMIN_PASS|g" .env
    sed -i "s|CHANGE_ME_agent_api_secret|$API_SECRET|g" .env
    sed -i "s|CHANGE_ME_webhook_secret|$WEBHOOK_SECRET|g" .env
    sed -i "s|CHANGE_ME_admin_password|$ADMIN_PASS|g" .env
    sed -i "s|CHANGE_ME_generate_with_openssl_rand_hex_32|$ADMIN_KEY|g" .env

    echo ""
    echo "  ⚠️  .env created with generated secrets."
    echo "  You MUST still set:"
    echo "    - DOMAIN"
    echo "    - TELEGRAM_BOT_TOKEN"
    echo "    - TELEGRAM_ADMIN_CHAT_ID"
    echo "    - OPENAI_API_KEY or ANTHROPIC_API_KEY"
    echo ""
    echo "  Edit: nano $INSTALL_DIR/.env"
else
    echo "  .env already exists, skipping."
fi

# 8. Set permissions
echo "[7/8] Setting permissions..."
chmod 600 "$INSTALL_DIR/.env"
chmod +x "$INSTALL_DIR/scripts/"*.sh 2>/dev/null || true

# 9. Also update the init-db.sql with actual passwords
echo "[8/8] Preparing database init..."
# The init-db.sh script reads from env, so the SQL file passwords are just placeholders
# Replace them to match
source "$INSTALL_DIR/.env" 2>/dev/null || true

echo ""
echo "==========================================="
echo "  Setup complete!"
echo "==========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env:  nano $INSTALL_DIR/.env"
echo "  2. Set DOMAIN, TELEGRAM_BOT_TOKEN, API keys"
echo "  3. Start system:  cd $INSTALL_DIR && docker compose up -d"
echo "  4. Check status:  docker compose ps"
echo "  5. View logs:     docker compose logs -f"
echo ""
echo "Optional: Setup HTTPS with certbot:"
echo "  certbot certonly --webroot -w $INSTALL_DIR/data/certbot/www -d yourdomain.com"
echo ""
