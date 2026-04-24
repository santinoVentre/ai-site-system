#!/bin/bash
# =============================================================
#  AI Site System — Backup Script
#  Creates timestamped backups of database, generated sites,
#  and configuration.
# =============================================================
set -euo pipefail

INSTALL_DIR="/opt/ai-site-system"
BACKUP_DIR="$INSTALL_DIR/data/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="backup_$TIMESTAMP"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

echo "Starting backup: $BACKUP_NAME"

mkdir -p "$BACKUP_PATH"

# 1. Database backup
echo "[1/4] Backing up PostgreSQL..."
docker compose -f "$INSTALL_DIR/docker-compose.yml" exec -T postgres \
    pg_dumpall -U postgres | gzip > "$BACKUP_PATH/database_full.sql.gz"

echo "[2/4] Backing up app database separately..."
source "$INSTALL_DIR/.env"
docker compose -f "$INSTALL_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U postgres "$APP_DB_NAME" | gzip > "$BACKUP_PATH/app_db.sql.gz"

# 2. Generated sites backup
echo "[3/4] Backing up generated sites..."
if [ -d "$INSTALL_DIR/data/generated-sites" ]; then
    tar czf "$BACKUP_PATH/generated-sites.tar.gz" \
        -C "$INSTALL_DIR/data" generated-sites/
fi

# 3. Config backup
echo "[4/4] Backing up configuration..."
cp "$INSTALL_DIR/.env" "$BACKUP_PATH/env.backup"
cp "$INSTALL_DIR/docker-compose.yml" "$BACKUP_PATH/docker-compose.yml.backup"
tar czf "$BACKUP_PATH/nginx-config.tar.gz" \
    -C "$INSTALL_DIR" nginx/

# Calculate size
BACKUP_SIZE=$(du -sh "$BACKUP_PATH" | cut -f1)
echo ""
echo "Backup complete: $BACKUP_PATH ($BACKUP_SIZE)"
echo ""

# Cleanup old backups (keep last 7)
echo "Cleaning up old backups (keeping last 7)..."
cd "$BACKUP_DIR"
ls -dt backup_*/ 2>/dev/null | tail -n +8 | xargs rm -rf 2>/dev/null || true

echo "Done."
