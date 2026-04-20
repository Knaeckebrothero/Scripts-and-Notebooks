# HACCP App Deployment

HACCP (Hazard Analysis Critical Control Points) compliance tracking app. Streamlit web application with PostgreSQL database.

## Prerequisites

1. Build and push the container image (see below)
2. Update secrets in `01-secrets.yaml` with secure passwords
3. Create DNS record in Cloudflare pointing `<YOUR_DOMAIN>` to your tunnel

## Build Container Image

```bash
# From the haccp_app directory
cd /path/to/haccp_app

# Build with Podman
podman build -t <YOUR_USERNAME>/haccp-app:latest -f deployment/Dockerfile .

# Push to Docker Hub
podman push <YOUR_USERNAME>/haccp-app:latest
```

## Deploy

```bash
# Apply all manifests (ordered by filename prefix)
kubectl apply -f deployments/haccp/

# Update Cloudflare Tunnel to pick up new route
kubectl -n cloudflare-tunnel rollout restart deployment cloudflared

# Verify deployment
kubectl get pods -n haccp
kubectl get svc -n haccp
```

## Verify

```bash
# Check PostgreSQL is running
kubectl logs -n haccp statefulset/haccp-postgres

# Check app is running
kubectl logs -n haccp deployment/haccp-app

# Check database initialization
kubectl logs -n haccp deployment/haccp-app -c haccp | head -50
```

## Access

- **URL**: https://<YOUR_DOMAIN>
- **Default admin**: admin / (see ADMIN_PASSWORD in secrets)

## Configuration

### Secrets (`01-secrets.yaml`)

Update these values before deploying:
- `POSTGRES_PASSWORD` / `DATABASE_PASSWORD`: Database password
- `ADMIN_PASSWORD`: Initial admin user password (change after first login)

### ConfigMap (`02-configmap.yaml`)

Non-sensitive configuration:
- Temperature thresholds (fridge/freezer min/max)
- Expiry warning days
- Session timeout and lockout settings

## Architecture

```
Internet
    |
Cloudflare Tunnel (haccp.hotelrheinland.com)
    |
haccp-app Service (ClusterIP:8501)
    |
haccp-app Deployment (Streamlit)
    |
haccp-postgres Service (ClusterIP:5432)
    |
haccp-postgres StatefulSet (PostgreSQL 16)
    |
Longhorn PVC (8Gi)
```

## Troubleshooting

```bash
# Check pod status
kubectl describe pod -n haccp -l app=haccp-app
kubectl describe pod -n haccp -l app=haccp-postgres

# Check PVC binding
kubectl get pvc -n haccp

# Test database connectivity from app pod
kubectl exec -it -n haccp deployment/haccp-app -- nc -zv haccp-postgres 5432

# Restart app to reinitialize database
kubectl rollout restart deployment/haccp-app -n haccp
```

## Cleanup

```bash
kubectl delete -f deployments/haccp/
```
