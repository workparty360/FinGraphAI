#!/usr/bin/env bash
# ==============================================================================
# FinGraph AI — FalkorDB Database Restore Script
# Restores FalkorDB volume from a specified .tar.gz backup archive.
# ==============================================================================

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path_to_backup_tar_gz>"
    echo "Example: $0 backups/falkordb_backup_20261017_120000.tar.gz"
    exit 1
fi

BACKUP_FILE="$(realpath "$1")"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "[-] Error: Backup file not found: $BACKUP_FILE"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/../../Fin_Model_123/docker-compose.prod.yml" ]; then
    APP_DIR="${SCRIPT_DIR}/../../Fin_Model_123"
elif [ -f "${SCRIPT_DIR}/../docker-compose.prod.yml" ]; then
    APP_DIR="${SCRIPT_DIR}/.."
else
    APP_DIR="$(pwd)"
fi

cd "$APP_DIR"

echo "===================================================================="
echo "  FinGraph AI — Restoring FalkorDB from Backup"
echo "  Archive: $BACKUP_FILE"
echo "===================================================================="

read -p "Are you sure you want to restore? This will overwrite the current database. (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Restore cancelled by operator."
    exit 0
fi

# Stop services before restoring volume
echo "[*] Stopping services..."
docker compose -f docker-compose.prod.yml stop app falkordb || true

VOLUME_NAME=$(docker volume ls --filter name=falkor_data -q | head -n1 || echo "fingraph_falkor_data")

echo "[*] Restoring data into volume '$VOLUME_NAME'..."
docker run --rm \
    -v "${VOLUME_NAME}:/volume" \
    -v "$(dirname "$BACKUP_FILE"):/backup" \
    alpine sh -c "rm -rf /volume/* && tar -xzf /backup/$(basename "$BACKUP_FILE") -C /volume"

echo "[*] Restarting FalkorDB and App services..."
docker compose -f docker-compose.prod.yml up -d falkordb app

# Verify FalkorDB health
for i in {1..30}; do
    STATUS=$(docker inspect --format='{{json .State.Health.Status}}' fingraph-falkordb 2>/dev/null || echo '"unknown"')
    if [ "$STATUS" = '"healthy"' ]; then
        echo "[+] FalkorDB is healthy and restored!"
        break
    fi
    sleep 2
done

echo "===================================================================="
echo "  Restore Completed Successfully!"
echo "===================================================================="
