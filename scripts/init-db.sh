#!/bin/bash
# =============================================================
#  Database init wrapper
#  Mounted as /docker-entrypoint-initdb.d/00-init.sh
#  Creates users, databases, then applies schema SQL
# =============================================================
set -e

APP_DB="${APP_DB_NAME:-ai_site_system}"
APP_USER="${APP_DB_USER:-app_user}"
N8N_DB="${N8N_DB_NAME:-n8n}"
N8N_USER="${N8N_DB_USER:-n8n_user}"

echo "=== Creating database users and databases ==="

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${APP_USER}') THEN
            CREATE ROLE ${APP_USER} WITH LOGIN PASSWORD '${APP_DB_PASSWORD}';
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${N8N_USER}') THEN
            CREATE ROLE ${N8N_USER} WITH LOGIN PASSWORD '${N8N_DB_PASSWORD}';
        END IF;
    END
    \$\$;

    SELECT 'CREATE DATABASE ${APP_DB} OWNER ${APP_USER}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${APP_DB}')
    \gexec

    SELECT 'CREATE DATABASE ${N8N_DB} OWNER ${N8N_USER}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${N8N_DB}')
    \gexec
EOSQL

echo "=== Applying schema to ${APP_DB} ==="

# Apply the schema SQL file to the app database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$APP_DB" \
    -f /docker-entrypoint-initdb.d/01-schema.sql

echo "=== Database initialization complete ==="
