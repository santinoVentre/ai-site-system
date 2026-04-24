#!/bin/bash
# =============================================================
#  AI Site System — Restore from Backup
#  Usage: ./restore.sh /path/to/backup_directory
# =============================================================
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 <backup_directory>"
    echo "Example: $0 /opt/ai-site-system/data/backups/backup_20260420_120000"
    exit 1
fi

BACKUP_PATH="$1"
INSTALL_DIR="/opt/ai-site-system"

if [ ! -d "$BACKUP_PATH" ]; then
    echo "Error: Backup directory not found: $BACKUP_PATH"
    exit 1
fi

echo "==========================================="
echo "  Restoring from: $BACKUP_PATH"
echo "==========================================="
echo ""
echo "WARNING: This will overwrite current data."
read -p "Continue? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

# 1. Restore database
if [ -f "$BACKUP_PATH/database_full.sql.gz" ]; then
    echo "[1/3] Restoring database..."
    gunzip -c "$BACKUP_PATH/database_full.sql.gz" | \
        docker compose -f "$INSTALL_DIR/docker-compose.yml" exec -T postgres \
        psql -U postgres
    echo "  Database restored."
fi

# 2. Restore generated sites
if [ -f "$BACKUP_PATH/generated-sites.tar.gz" ]; then
    echo "[2/3] Restoring generated sites..."
    tar xzf "$BACKUP_PATH/generated-sites.tar.gz" -C "$INSTALL_DIR/data/"
    echo "  Generated sites restored."
fi

# 3. Restore config
if [ -f "$BACKUP_PATH/env.backup" ]; then
    echo "[3/3] Restoring configuration..."
    cp "$BACKUP_PATH/env.backup" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    echo "  Configuration restored."
fi

echo ""
echo "Restore complete. Restart services:"
echo "  cd $INSTALL_DIR && docker compose restart"
