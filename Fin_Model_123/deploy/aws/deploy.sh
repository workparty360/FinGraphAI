#!/usr/bin/env bash
# ==============================================================================
# FinGraph AI — Production Deployment Script (AWS EC2)
# Hardened against failures; performs health checks and seeds FalkorDB.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Verify whether we are in Fin_Model_123 or repo root
if [ -f "${SCRIPT_DIR}/../../Fin_Model_123/docker-compose.prod.yml" ]; then
    APP_DIR="${SCRIPT_DIR}/../../Fin_Model_123"
elif [ -f "${SCRIPT_DIR}/../docker-compose.prod.yml" ]; then
    APP_DIR="${SCRIPT_DIR}/.."
else
    APP_DIR="$(pwd)"
fi

cd "$APP_DIR"

echo "===================================================================="
echo "  Deploying FinGraph AI (AWS EC2 Production)"
echo "  Target Directory: $APP_DIR"
echo "===================================================================="

# 1. Environment file validation
if [ ! -f .env ]; then
    echo "[-] Error: .env file not found in $APP_DIR"
    echo "    Please create .env from .env.example before deploying:"
    echo "    cp .env.example .env && nano .env"
    exit 1
fi

# Check for placeholder keys
if grep -q "gsk_your_groq_api_key" .env; then
    echo "[!] Warning: .env contains placeholder AI API keys. Please update .env with valid credentials."
fi

# 2. Build production images
echo "[*] Building Docker containers (app, caddy, falkordb)..."
docker compose -f docker-compose.prod.yml build --pull

# 3. Start FalkorDB and App stack
echo "[*] Launching services..."
docker compose -f docker-compose.prod.yml up -d falkordb app caddy

# 4. Wait for FalkorDB health check
echo "[*] Waiting for FalkorDB to report healthy..."
for i in {1..30}; do
    STATUS=$(docker inspect --format='{{json .State.Health.Status}}' fingraph-falkordb 2>/dev/null || echo '"unknown"')
    if [ "$STATUS" = '"healthy"' ]; then
        echo "[+] FalkorDB is healthy!"
        break
    fi
    echo "    Waiting for FalkorDB ($i/30)..."
    sleep 2
done

# 5. Initialize and seed FalkorDB Financial Knowledge Graph
echo "[*] Initializing FalkorDB schema and seeding financial knowledge graph..."
docker compose -f docker-compose.prod.yml run --rm seeder

# 6. Verify App health check
echo "[*] Verifying FinGraph AI application status..."
for i in {1..30}; do
    APP_STATUS=$(docker inspect --format='{{json .State.Health.Status}}' fingraph-app 2>/dev/null || echo '"unknown"')
    if [ "$APP_STATUS" = '"healthy"' ]; then
        echo "[+] FinGraph AI application is healthy and running!"
        break
    fi
    echo "    Waiting for application startup ($i/30)..."
    sleep 2
done

echo "===================================================================="
echo "  Deployment Complete!"
docker compose -f docker-compose.prod.yml ps
echo ""
DOMAIN=$(grep '^APP_DOMAIN=' .env 2>/dev/null | cut -d '=' -f2- || echo "localhost")
echo "  Web Application: https://${DOMAIN}"
echo "  Logs: docker compose -f docker-compose.prod.yml logs -f app"
echo "===================================================================="
