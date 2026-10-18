#!/usr/bin/env bash
# ==============================================================================
# FinGraph AI — AWS EC2 Instance Provisioning & Environment Setup Script
# Target: Ubuntu Server 22.04 / 24.04 LTS (x86_64 / arm64)
# ==============================================================================

set -euo pipefail

echo "===================================================================="
echo "  FinGraph AI — AWS EC2 Host Setup"
echo "===================================================================="

# Check root or sudo
if [ "$(id -u)" -ne 0 ]; then
    echo "[-] This script must be run as root or with sudo: sudo ./setup.sh"
    exit 1
fi

CURRENT_USER="${SUDO_USER:-$USER}"

echo "[*] Updating apt package cache..."
apt-get update -y
apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    ufw \
    tar \
    gzip

# System Resource Check
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
echo "[*] Total RAM detected: ${TOTAL_RAM_MB}MB"
if [ "$TOTAL_RAM_MB" -lt 3500 ]; then
    echo "[!] Warning: Host has less than 4GB RAM. Adding a 2GB swapfile to prevent OOM..."
    if [ ! -f /swapfile ]; then
        fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
        echo "[+] 2GB swapfile active."
    fi
fi

# Install Docker Engine and Docker Compose Plugin
if ! command -v docker &> /dev/null; then
    echo "[*] Installing official Docker Engine..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
    echo "[+] Docker Engine installed."
else
    echo "[+] Docker Engine is already installed."
fi

# Add deploy user to docker group
if ! groups "$CURRENT_USER" | grep -q '\bdocker\b'; then
    usermod -aG docker "$CURRENT_USER"
    echo "[+] User '$CURRENT_USER' added to 'docker' group."
fi

# Configure host firewall (UFW)
echo "[*] Configuring UFW firewall rules for public ports (22, 80, 443)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP redirect'
ufw allow 443/tcp comment 'HTTPS TLS'
# Strictly do NOT allow 6379 or 8501 externally
ufw deny 6379/tcp comment 'Block FalkorDB external' || true
ufw deny 8501/tcp comment 'Block Streamlit direct external' || true

# Enable UFW non-interactively
ufw --force enable

echo "===================================================================="
echo "  Setup Complete!"
echo "  - Docker & Docker Compose: Verified"
echo "  - Inbound Ports Open: 22 (SSH), 80 (HTTP), 443 (HTTPS)"
echo "  - Private Ports Closed: 6379 (FalkorDB), 8501 (Streamlit)"
echo "  Please log out and log back in (or run 'newgrp docker') to refresh group permissions."
echo "===================================================================="
