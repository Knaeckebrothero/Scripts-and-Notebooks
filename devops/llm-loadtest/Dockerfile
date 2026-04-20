FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    openfortivpn \
    supervisor \
    iptables \
    iproute2 \
    iputils-ping \
    curl \
    ca-certificates \
    ppp \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Install Python dependencies
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY entrypoint.sh /entrypoint.sh

# Create directories
RUN mkdir -p /app/data /app/certs /var/log/supervisor

# Copy prompts dataset
COPY data/prompts.json /app/data/prompts.json

# Copy certificates directory (contains .gitkeep by default)
# To add the GEANT certificate, download geant_chain.pem and place in certs/:
#   curl -o certs/geant_chain.pem "https://crt.sh/?d=2475254782"
# Or mount it at runtime via k8s secret/configmap
COPY certs/ /app/certs/

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

# Expose Streamlit port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
