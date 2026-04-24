# AI Site System — Backup & Rollback Guide

## Automated Backups

### Run backup

```bash
cd /opt/ai-site-system
bash scripts/backup.sh
```

This creates a timestamped backup at `data/backups/backup_YYYYMMDD_HHMMSS/` containing:
- `database_full.sql.gz` — Full PostgreSQL dump (all databases)
- `app_db.sql.gz` — Application database only
- `generated-sites.tar.gz` — All generated website files
- `env.backup` — Current environment configuration
- `docker-compose.yml.backup` — Current compose file
- `nginx-config.tar.gz` — Nginx configuration

Old backups are automatically pruned (last 7 kept).

### Schedule automatic backups (cron)

```bash
# Edit crontab
crontab -e

# Add daily backup at 3 AM
0 3 * * * /opt/ai-site-system/scripts/backup.sh >> /var/log/ai-site-backup.log 2>&1
```

## Restore from Backup

```bash
bash scripts/restore.sh /opt/ai-site-system/data/backups/backup_YYYYMMDD_HHMMSS

# Then restart services
docker compose restart
```

## Project-Level Rollback

The system supports per-project revision rollback without affecting other projects.

### Via Telegram

```
/projects              # List projects
/approve <project_id>  # Approve current revision
/reject <project_id>   # Reject and rollback
```

### Via API

```bash
# Rollback to a specific revision
curl -X POST http://localhost:8000/projects/{project_id}/rollback \
    -H "X-API-Secret: YOUR_SECRET" \
    -H "Content-Type: application/json" \
    -d '{"revision_id": "UUID_OF_TARGET_REVISION"}'
```

### Via Admin Dashboard

1. Navigate to the project detail page
2. Find the revision in the revision history
3. Click "Rollback to this revision"

## Git-Level Rollback

Each project has its own Git repository at `data/generated-sites/{slug}/`.

```bash
# Enter project directory
cd /opt/ai-site-system/data/generated-sites/project-slug/

# View revision history
git log --oneline

# The system uses branches: main, revision-1, revision-2, etc.
git branch -a

# Manual rollback (not recommended — use API instead)
git checkout main
git reset --hard <commit_hash>
```

## Database-Only Restore

```bash
# Restore just the app database
gunzip -c /path/to/backup/app_db.sql.gz | \
    docker compose exec -T postgres psql -U postgres -d ai_site_system
```

## Disaster Recovery

Full system recovery on a new VPS:

1. Provision new Ubuntu 24.04 VPS
2. Run `scripts/setup-vps.sh`
3. Copy backup files to new server
4. Run `scripts/restore.sh /path/to/backup`
5. Update DNS to point to new IP
6. Run `scripts/setup-telegram-webhook.sh`
7. Verify with the post-install checklist in DEPLOYMENT.md
