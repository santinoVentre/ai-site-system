# AI Site System — Deployment Guide (Ubuntu 24.04 VPS)

## Prerequisites

- Ubuntu 24.04 LTS VPS (minimum 2 vCPU, 4 GB RAM, 40 GB SSD)
- Domain name pointed to the VPS IP
- Root or sudo access
- Telegram bot token (from @BotFather)
- OpenAI and/or Anthropic API key

## Quick Start

### 1. Upload project files

```bash
# On your local machine, upload to VPS:
scp -r . root@YOUR_VPS_IP:/opt/ai-site-system/

# Or clone from git:
ssh root@YOUR_VPS_IP
cd /opt
git clone YOUR_REPO_URL ai-site-system
```

### 2. Run setup script

```bash
cd /opt/ai-site-system
chmod +x scripts/setup-vps.sh
sudo bash scripts/setup-vps.sh
```

This installs Docker, creates directories, and generates secure passwords in `.env`.

### 3. Configure environment

```bash
nano /opt/ai-site-system/.env
```

**Required settings to change:**

| Variable | Description |
|----------|-------------|
| `DOMAIN` | Your domain (e.g., `sites.example.com`) |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_ADMIN_CHAT_ID` | Your Telegram user ID (use @userinfobot) |
| `OPENAI_API_KEY` | OpenRouter API key (from openrouter.ai/keys) |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` (default) |
| `DEFAULT_LLM_MODEL` | `openai/gpt-4o`, `anthropic/claude-sonnet-4`, etc. |

### 4. Start the system

```bash
cd /opt/ai-site-system
docker compose up -d
```

### 5. Verify startup

```bash
# Check all containers are running
docker compose ps

# Expected: all services show "Up (healthy)" or "Up"
# Wait ~60 seconds for healthchecks to pass

# Check logs for errors
docker compose logs --tail=50

# Test API health
curl http://localhost:8000/health
```

### 6. Setup Telegram webhook

```bash
chmod +x scripts/setup-telegram-webhook.sh
bash scripts/setup-telegram-webhook.sh
```

### 7. Setup HTTPS (recommended)

```bash
# Get SSL certificate via certbot
certbot certonly --webroot \
    -w /opt/ai-site-system/data/certbot/www \
    -d yourdomain.com

# Update nginx config to enable SSL
# Uncomment the SSL server block in nginx/conf.d/default.conf
# Update certificate paths
docker compose restart nginx
```

## Post-Install Verification Checklist

Run these commands to verify the system is operational:

```bash
# 1. All containers running
docker compose ps
# Expected: 7 services UP

# 2. Database accessible
docker compose exec postgres psql -U postgres -d ai_site_system -c "SELECT COUNT(*) FROM users;"
# Expected: 1 (admin user)

# 3. Agent API health
curl -s http://localhost:8000/health | jq .
# Expected: {"status": "healthy", ...}

# 4. Redis connectivity
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" ping
# Expected: PONG

# 5. n8n accessible
curl -s http://localhost:5678/healthz
# Expected: {"status": "ok"}

# 6. Telegram webhook set
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo" | jq .
# Expected: url matches your domain

# 7. Admin dashboard accessible
curl -s -o /dev/null -w "%{http_code}" http://localhost:8002/login
# Expected: 200

# 8. QA runner ready
curl -s http://localhost:8001/health | jq .
# Expected: {"status": "healthy"}

# 9. Test Telegram bot
# Send /start to your bot in Telegram
# Expected: Welcome message

# 10. Test create workflow
# Send /new "Test landing page for a coffee shop" to bot
# Expected: Job creation confirmation
```

## Service Management

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# Restart a single service
docker compose restart agent-api

# View logs
docker compose logs -f agent-api
docker compose logs -f --tail=100

# Rebuild after code changes
docker compose build agent-api
docker compose up -d agent-api

# Rebuild all
docker compose build
docker compose up -d

# Shell into a container
docker compose exec agent-api bash
docker compose exec postgres psql -U postgres
```

## Updating

```bash
cd /opt/ai-site-system

# 1. Backup first
bash scripts/backup.sh

# 2. Pull updates
git pull

# 3. Rebuild and restart
docker compose build
docker compose up -d

# 4. Verify
docker compose ps
```

## Firewall Setup

```bash
# Allow only HTTP, HTTPS, and SSH
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

## Resource Monitoring

```bash
# Container resource usage
docker stats

# Disk usage
du -sh /opt/ai-site-system/data/*

# Database size
docker compose exec postgres psql -U postgres -d ai_site_system \
    -c "SELECT pg_size_pretty(pg_database_size('ai_site_system'));"
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Container keeps restarting | `docker compose logs SERVICE_NAME` to check errors |
| Database connection refused | Wait for postgres healthcheck, check passwords in .env |
| Telegram webhook not working | Run `scripts/setup-telegram-webhook.sh`, check DOMAIN |
| n8n can't connect to DB | Verify N8N_DB_* vars match postgres init |
| QA runner fails | Playwright needs `--ipc=host`, check docker-compose security_opt |
| Agent API 500 errors | Check LLM API keys, `docker compose logs agent-api` |
| Preview not loading | Check nginx conf, verify files exist in data/generated-sites |
