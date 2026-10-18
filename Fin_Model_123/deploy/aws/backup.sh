#!/usr/bin/env bash
# ==============================================================================
# FinGraph AI — FalkorDB Database Backup Script
# Creates an atomic compressed archive of the persistent FalkorDB volume.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/../../Fin_Model_123/docker-compose.prod.yml" ]; then
    APP_DIR="${SCRIPT_DIR}/../../Fin_Model_123"
elif [ -f "${SCRIPT_DIR}/../docker-compose.prod.yml" ]; then
    APP_DIR="${SCRIPT_DIR}/.."
else
    APP_DIR="$(pwd)"
fi

BACKUP_DIR="${APP_DIR}/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/falkordb_backup_${TIMESTAMP}.tar.gz"

echo "===================================================================="
echo "  FinGraph AI — FalkorDB Backup"
echo "  Timestamp: $TIMESTAMP"
echo "===================================================================="

# Trigger Redis SAVE / BGSAVE via redis-cli inside container to ensure disk sync
if docker ps --format '{{.Names}}' | grep -q "fingraph-falkordb"; then
    echo "[*] Triggering database sync to disk..."
    docker exec fingraph-falkordb redis-cli bgsave || true
    sleep 3
fi

# Detect volume name
VOLUME_NAME=$(docker volume ls --filter name=falkor_data -q | head -n1 || echo "fingraph_falkor_data")

echo "[*] Archiving persistent volume '$VOLUME_NAME' to $BACKUP_FILE..."
docker run --rm \
    -v "${VOLUME_NAME}:/volume:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar -czf "/backup/falkordb_backup_${TIMESTAMP}.tar.gz" -C /volume .

chmod 600 "$BACKUP_FILE"

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[+] Backup successfully created: $BACKUP_FILE ($FILE_SIZE)"

# Retain last 7 backups, purge older ones
find "$BACKUP_DIR" -type f -name "falkordb_backup_*.tar.gz" -mtime +7 -delete 2>/dev/null || true
echo "===================================================================="
