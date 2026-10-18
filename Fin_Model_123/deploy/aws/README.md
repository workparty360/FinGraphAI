# 🚀 FinGraph AI — AWS EC2 Deployment Guide

Complete step-by-step deployment and operations guide for deploying **FinGraph AI** on an Amazon Web Services (AWS) EC2 virtual machine using Docker Compose and Caddy automatic HTTPS.

---

## 1. Architecture Overview

```
                      [ Client Web Browsers ]
                                |
                                v (HTTPS 443 / HTTP 80 redirect)
    =================== AWS EC2 INSTANCE ===================
    |                                                      |
    |   +----------------------------------------------+   |
    |   |         Caddy Reverse Proxy (Alpine)         |   |
    |   |     Automatic TLS · Security Headers · WSS    |   |
    |   +----------------------------------------------+   |
    |                          |                           |
    |            Docker Private Bridge Network             |
    |                          |                           |
    |   +----------------------------------------------+   |
    |   |      FinGraph AI Application (Port 8501)     |   |
    |   |  - Streamlit Full-Stack UI                   |   |
    |   |  - LangGraph Finance Agent Team              |   |
    |   |  - 3-Key API Failover Manager                |   |
    |   +----------------------------------------------+   |
    |                          |                           |
    |                          v (TCP 6379 - Private)      |
    |   +----------------------------------------------+   |
    |   |      FalkorDB Graph Database Engine          |   |
    |   |  - Connected Financial Knowledge Graph       |   |
    |   |  - Persistent AOF Storage (falkor_data)      |   |
    |   +----------------------------------------------+   |
    ========================================================
```

---

## 2. EC2 Instance Sizing & Prerequisites

| Specification | Minimum (Demo / Dev) | Recommended (Production) |
|---|---|---|
| **Instance Type** | `t3.medium` (2 vCPU, 4GB RAM) | `t3.large` (2 vCPU, 8GB RAM) |
| **Operating System** | Ubuntu Server 24.04 LTS (64-bit x86) | Ubuntu Server 24.04 LTS (64-bit x86) |
| **Storage (EBS)** | 30 GB `gp3` (3000 IOPS, 125 MB/s) | 50 GB `gp3` (3000 IOPS, 125 MB/s) |
| **Architecture** | `x86_64` (AMD64) or `arm64` (Graviton) | `x86_64` (AMD64) |

> [!NOTE]
> FalkorDB runs in-memory with Append-Only File (AOF) disk persistence. A minimum of 4GB RAM ensures smooth memory allocation for FalkorDB graph structures, embeddings, and Streamlit worker processes.

---

## 3. AWS Security Group Configuration

Create an EC2 Security Group with the following inbound rules:

| Type | Port Range | Protocol | Source | Purpose |
|---|---|---|---|---|
| **SSH** | `22` | TCP | `Your_Trusted_IP/32` (or use AWS SSM) | Server administration |
| **HTTP** | `80` | TCP | `0.0.0.0/0` | Let's Encrypt ACME challenge & HTTP->HTTPS redirect |
| **HTTPS** | `443` | TCP | `0.0.0.0/0` | Secure public application traffic |

> [!CAUTION]
> **Strictly DO NOT open the following ports to the internet:**
> - Port `6379` (FalkorDB) — Must remain strictly internal within the Docker network.
> - Port `8501` (Streamlit backend) — All traffic must route through Caddy on 443.

---

## 4. Step-by-Step Deployment Instructions

### Step 1: Connect to your EC2 Instance
```bash
ssh -i /path/to/your-key.pem ubuntu@<your-ec2-public-ip>
```

### Step 2: Clone the Repository
```bash
git clone https://github.com/your-username/FinGraph-AI.git
cd FinGraph-AI/Fin_Model_123
```

### Step 3: Run Host Provisioning
Run the automated host setup script to install Docker, Docker Compose, configure UFW firewall rules, and allocate a 2GB swapfile:
```bash
sudo ./deploy/aws/setup.sh
```
Refresh group permissions:
```bash
newgrp docker
```

### Step 4: Configure Production Environment (`.env`)
Copy the template and supply your domain and AI API keys:
```bash
cp .env.example .env
nano .env
```
Ensure the following variables are configured:
```ini
APP_DOMAIN=finance.yourdomain.com
AI_API_KEY_1=gsk_your_groq_api_key_primary
AI_API_KEY_2=gsk_your_groq_api_key_backup_1
AI_API_KEY_3=gsk_your_groq_api_key_backup_2
FALKORDB_HOST=falkordb
FALKORDB_PORT=6379
FALKORDB_GRAPH=financial_knowledge
```

### Step 5: Execute Automated Deployment
```bash
./deploy/aws/deploy.sh
```
The script will:
1. Validate your `.env` configuration.
2. Build the production application container.
3. Start the Docker Compose stack (`falkordb`, `app`, `caddy`).
4. Wait for FalkorDB health check confirmation.
5. Initialize graph schema indexes and seed permitted public financial data (AAPL, MSFT, GOOGL, NVDA, RELIANCE, TCS).
6. Verify application health check (`/_stcore/health`).
7. Report live status.

---

## 5. Operations & Maintenance

### Checking Logs
```bash
# View application logs
docker compose -f docker-compose.prod.yml logs -f app

# View FalkorDB logs
docker compose -f docker-compose.prod.yml logs -f falkordb

# View Caddy TLS / proxy logs
docker compose -f docker-compose.prod.yml logs -f caddy
```

### Performing Safe Rolling Updates
To update application code without touching persistent database volumes:
```bash
./deploy/aws/update.sh
```

### Creating Database Backups
To create an atomic, timestamped backup archive of the FalkorDB volume:
```bash
./deploy/aws/backup.sh
```
Backups are saved to `backups/falkordb_backup_<YYYYMMDD_HHMMSS>.tar.gz` with strict `600` permissions.

### Restoring from Backup
```bash
./deploy/aws/restore.sh backups/falkordb_backup_20261017_150000.tar.gz
```

---

## 6. Disaster Recovery & Troubleshooting

### If Caddy Cannot Obtain a TLS Certificate
1. Verify that your DNS `A` record for `APP_DOMAIN` points directly to your EC2 public IPv4 address.
2. Verify that AWS Security Group inbound port `80` is open to `0.0.0.0/0`.
3. Check Caddy logs:
   ```bash
   docker compose -f docker-compose.prod.yml logs caddy
   ```

### If FalkorDB Is Reported Offline
1. Check container health:
   ```bash
   docker ps --filter "name=fingraph-falkordb"
   ```
2. Inspect FalkorDB logs:
   ```bash
   docker compose -f docker-compose.prod.yml logs falkordb
   ```
3. Restart the database:
   ```bash
   docker compose -f docker-compose.prod.yml restart falkordb
   ```
