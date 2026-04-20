# LLM Load Tester

A containerized load testing tool for OpenAI-compatible LLM endpoints with a Streamlit dashboard.

## Features

- **Async Load Engine**: Poisson-distributed request scheduling for realistic load patterns
- **Multi-Endpoint Support**: Test multiple LLM endpoints simultaneously
- **Streaming Support**: Configurable mix of streaming (70%) and non-streaming (30%) requests
- **Real-time Metrics**: SQLite-backed metrics with live dashboard
- **VPN Integration**: Built-in openfortivpn support for Frankfurt UAS VPN
- **Kubernetes Ready**: Deployment manifests included

## Quick Start

### Local Development (without VPN)

```bash
cd devops/llm-loadtest
pip install -r app/requirements.txt
streamlit run app/main.py
```

### Build Container

```bash
# With Podman
podman build -t llm-loadtest .

# With Docker
docker build -t llm-loadtest .
```

### Run Container (No VPN)

```bash
podman run -p 8501:8501 \
  -e VPN_ENABLED=false \
  -v ./data:/app/data \
  llm-loadtest
```

### Run Container (With Frankfurt UAS VPN)

```bash
podman run -p 8501:8501 \
  --cap-add=NET_ADMIN \
  -e VPN_ENABLED=true \
  -e VPN_HOST=vpngate.frankfurt-university.de \
  -e VPN_PORT=443 \
  -e VPN_REALM=pub-all \
  -e VPN_USER=your-it-account \
  -e VPN_PASSWORD=your-password \
  -v ./data:/app/data \
  llm-loadtest
```

## VPN Setup (Frankfurt UAS)

The tool uses `openfortivpn` to connect to the university VPN.

### Required Parameters

| Parameter | Value |
|-----------|-------|
| Host | `vpngate.frankfurt-university.de` |
| Port | `443` |
| Realm | `pub-all` |
| Username | Your IT account |
| Password | Your IT password |

### Certificate Setup

The VPN requires the GEANT OV RSA CA 4 certificate chain. Two options:

**Option 1: Bake into image**
```bash
# Download certificate chain
curl -o certs/geant_chain.pem "https://crt.sh/?d=2475254782"

# Build image (certificate will be included)
podman build -t llm-loadtest .
```

**Option 2: Mount at runtime (Kubernetes)**
```bash
# Download certificate
curl -o geant_chain.pem "https://crt.sh/?d=2475254782"

# Create ConfigMap
kubectl create configmap geant-cert \
  --from-file=geant_chain.pem \
  -n llm-loadtest
```

## Kubernetes Deployment

1. **Download the GEANT certificate**:
   ```bash
   curl -o geant_chain.pem "https://crt.sh/?d=2475254782"
   kubectl create namespace llm-loadtest
   kubectl create configmap geant-cert --from-file=geant_chain.pem -n llm-loadtest
   ```

2. **Edit the secret** in `k8s/deployment.yaml` with your VPN credentials

3. **Apply the manifests**:
   ```bash
   kubectl apply -f k8s/deployment.yaml
   ```

4. **Access the dashboard**:
   ```bash
   # Via NodePort (default: 30851)
   http://<node-ip>:30851

   # Or via port-forward
   kubectl port-forward -n llm-loadtest svc/llm-loadtest 8501:8501
   ```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VPN_ENABLED` | `false` | Enable VPN connection |
| `VPN_HOST` | `vpngate.frankfurt-university.de` | VPN server hostname |
| `VPN_PORT` | `443` | VPN server port |
| `VPN_REALM` | `pub-all` | VPN realm (authentication domain) |
| `VPN_USER` | - | VPN username (IT account) |
| `VPN_PASSWORD` | - | VPN password |
| `ENDPOINTS` | - | Comma-separated endpoints (format: `name:url`) |
| `REQUESTS_PER_SECOND` | `50` | Default requests per second per endpoint |
| `STREAMING_RATIO` | `0.7` | Ratio of streaming requests (0.0-1.0) |
| `DURATION_HOURS` | `72` | Total test duration |
| `REQUEST_TIMEOUT` | `120` | Request timeout in seconds |
| `MAX_CONCURRENT_REQUESTS` | `500` | Maximum concurrent requests |

### Target Endpoints (University LLM Servers)

| Model | URL |
|-------|-----|
| Llama 3.1 8B | `http://10.18.2.105:9000` |
| Phi3 14B | `http://10.18.2.105:9001` |
| OSS 20B | `http://10.18.2.105:9002` |

## Dashboard Tabs

### Control Panel
- Start/Stop/Pause load test
- Per-endpoint rate configuration (0-100 req/s)
- Enable/disable individual endpoints
- Adjust streaming ratio

### Live Metrics
- Real-time request counts and success rates
- Response time percentiles (P50, P90, P95, P99)
- Per-endpoint performance breakdown
- Time series charts (auto-refresh when running)

### Analysis
- Historical statistics
- Response time distribution histogram
- Streaming vs non-streaming comparison
- Error analysis by endpoint
- CSV export

## Metrics Collected

- **Request timestamp**
- **Endpoint name**
- **Streaming mode** (true/false)
- **Prompt tokens**
- **Completion tokens**
- **Time to first token** (streaming only)
- **Total response time**
- **Status** (success/error/timeout)
- **Error message** (if applicable)
- **HTTP status code**

## Architecture

```
Container
├── entrypoint.sh ─────── VPN connection + startup
├── supervisord ──────── Process management
│   └── streamlit ────── Dashboard + async load engine
└── SQLite (WAL mode) ── Metrics storage
```

## Load Distribution

Uses a **Poisson process** for realistic random arrivals:
- Inter-arrival times follow exponential distribution
- Natural randomness without pre-computing timestamps
- Memory efficient for long-running tests (72+ hours)

Expected load: 100 req/s per endpoint × 3 endpoints = 300 req/s total.
Over 72 hours: ~77 million requests.

## Troubleshooting

### VPN Connection Failed

Check the container logs:
```bash
kubectl logs -n llm-loadtest deployment/llm-loadtest | grep -A 20 "Starting VPN"
```

Common issues:
- Wrong credentials (check IT account username/password)
- Missing GEANT certificate
- Network/firewall blocking port 443

### Cannot Reach LLM Servers

Verify VPN routing:
```bash
kubectl exec -n llm-loadtest deployment/llm-loadtest -- ip route
kubectl exec -n llm-loadtest deployment/llm-loadtest -- ping -c 3 10.18.2.105
```

### High Memory Usage

The database uses sampling for percentile calculations (10K samples max).
For very long tests, consider exporting and clearing metrics periodically via the Analysis tab.
