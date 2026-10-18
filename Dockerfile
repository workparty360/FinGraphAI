# ==============================================================================
# FinGraph AI — Multi-stage Production Dockerfile
# Tested for AMD64 and ARM64 Ubuntu / Debian
# ==============================================================================

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Install minimal OS dependencies for network probes and building wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create unprivileged system user for security
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -s /bin/bash -m appuser

# Copy application files
COPY . .

# Set permissions
RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "final.py"]
