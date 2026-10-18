#!/usr/bin/env bash
# ==============================================================================
# FinGraph AI — Safe Application Update Script (AWS EC2)
# Rebuilds and updates application containers without touching FalkorDB volumes.
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

cd "$APP_DIR"

echo "===================================================================="
echo "  Updating FinGraph AI (Safe Rolling Update)"
echo "  Database volumes (fingraph_falkor_data) will be strictly preserved."
echo "===================================================================="

# Pull code if inside git
if [ -d .git ]; then
    echo "[*] Pulling latest changes from Git repository..."
    git pull origin main || echo "[!] git pull skipped or failed, proceeding with local build."
fi

# Rebuild only the app container
echo "[*] Rebuilding application container..."
docker compose -f docker-compose.prod.yml build app

# Gracefully recreate and restart app
echo "[*] Restarting application service..."
docker compose -f docker-compose.prod.yml up -d --no-deps app

# Verify health
echo "[*] Verifying application health check..."
for i in {1..30}; do
    APP_STATUS=$(docker inspect --format='{{json .State.Health.Status}}' fingraph-app 2>/dev/null || echo '"unknown"')
    if [ "$APP_STATUS" = '"healthy"' ]; then
        echo "[+] Updated FinGraph AI application is healthy and active!"
        break
    fi
    sleep 2
done

echo "===================================================================="
echo "  Update Complete!"
docker compose -f docker-compose.prod.yml ps
echo "===================================================================="
