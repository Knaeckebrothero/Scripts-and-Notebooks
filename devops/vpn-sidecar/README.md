# VPN Sidecar

A tiny container that exposes a **SOCKS5 proxy** (and optionally a TCP forward) over a **FortiClient VPN** tunnel. Drop it next to a workload, point the workload's proxy settings at it, and the workload's traffic is tunneled through the VPN — without giving the workload itself `NET_ADMIN` or forcing it to speak the VPN protocol.

Built from:
- [`openfortivpn`](https://github.com/adrienverge/openfortivpn) — client for FortiGate SSL VPN
- [`microsocks`](https://github.com/rofl0r/microsocks) — ~100-line SOCKS5 server in C
- [`socat`](http://www.dest-unreach.org/socat/) — optional TCP port forwarder

The entrypoint supervises both processes and auto-reconnects. FortiGate servers typically drop sessions after ~5h; the loop distinguishes between clean drops (fast reconnect) and config/auth errors (exponential backoff).

---

## Quick start

Pull the pre-built image from GHCR and run it:

```bash
docker run -d --name vpn-sidecar \
  --cap-add=NET_ADMIN \
  --device=/dev/ppp \
  -e VPN_HOST=vpn.example.com \
  -e VPN_USER=your_username \
  -e VPN_PASS=your_password \
  -e VPN_TRUSTED_CERT=<64-char-hash> \
  -p 1080:1080 \
  ghcr.io/knaeckebrothero/vpn-sidecar:latest
```

Then point any client at `socks5://localhost:1080`:

```bash
curl --socks5 localhost:1080 https://internal.example.com
```

---

## Configuration

The sidecar reads its openfortivpn config from `/etc/openfortivpn/config`. You can provide it two ways.

### Option A — environment variables (recommended for containerized workflows)

| Var | Required | Default | Purpose |
| --- | --- | --- | --- |
| `VPN_HOST` | yes | — | VPN gateway hostname |
| `VPN_PORT` | no | `443` | VPN gateway port |
| `VPN_USER` | yes | — | Username |
| `VPN_PASS` | yes | — | Password |
| `VPN_REALM` | no | — | FortiGate realm (e.g. `pub-all`) |
| `VPN_TRUSTED_CERT` | no | — | SHA-256 hash of the gateway cert (64 hex chars) |
| `SOCKS_PORT` | no | `1080` | Port microsocks listens on |
| `FORWARD_PORT` | no | — | If set, socat listens here and forwards to `FORWARD_TARGET` over the VPN |
| `FORWARD_TARGET` | no | — | `host:port` target (only reachable inside the VPN) |
| `HEALTH_PORT` | no | `8081` | Port the HTTP health endpoint listens on (any path returns the verdict) |

If `VPN_TRUSTED_CERT` is not set, you can instead mount a CA file at `/etc/vpn/certs/ca.crt` — the entrypoint will pick it up automatically and prefer it over the hash.

### Option B — mounted config file

Copy `config.example` and fill in your credentials:

```bash
cp devops/vpn-sidecar/config.example devops/vpn-sidecar/config
# edit devops/vpn-sidecar/config
chmod 600 devops/vpn-sidecar/config
```

Then bind-mount it into the container:

```bash
docker run -d --name vpn-sidecar \
  --cap-add=NET_ADMIN --device=/dev/ppp \
  -v $PWD/devops/vpn-sidecar/config:/etc/openfortivpn/config:ro \
  -p 1080:1080 \
  ghcr.io/knaeckebrothero/vpn-sidecar:latest
```

The `config` file is gitignored; only `config.example` is tracked.

### Finding the `trusted-cert` hash

`openfortivpn` needs to pin the gateway's TLS certificate. If you don't have the hash, just run it once without it — the error output prints what to use:

```
ERROR: Gateway certificate validation failed, and the certificate digest is
not in the local whitelist. If you trust it, rerun with:
  --trusted-cert <hash>
```

Copy that 64-character hex string into `VPN_TRUSTED_CERT` or the `trusted-cert = ...` line in the config file.

---

## Runtime requirements

Both needed — the tunnel cannot come up without them:

- **`NET_ADMIN` capability** — openfortivpn creates a `ppp0` interface and installs routes.
- **`/dev/ppp` device** — needed by `pppd` for the PPP tunnel.

Most container hosts have the `ppp` kernel module loaded by default. If not:

```bash
sudo modprobe ppp_generic
```

For Podman, add `--security-opt label=disable` if SELinux is blocking `/dev/ppp`.

---

## Using it with other containers

### TCP forward mode

If the client you want to route through the VPN can't be configured to speak SOCKS5 (e.g. it expects a plain HTTP endpoint on a fixed host:port), use `FORWARD_PORT` / `FORWARD_TARGET`. `socat` listens on the forward port and pipes traffic to the target over the tunnel:

```bash
docker run -d --name vpn-api \
  --cap-add=NET_ADMIN --device=/dev/ppp \
  -e VPN_HOST=... -e VPN_USER=... -e VPN_PASS=... \
  -e FORWARD_PORT=8080 \
  -e FORWARD_TARGET=api-internal.example.com:8080 \
  -p 8080:8080 \
  ghcr.io/knaeckebrothero/vpn-sidecar:latest
```

The client then talks to `http://vpn-api:8080` as if it were talking to `api-internal.example.com:8080` directly.

### Docker Compose

```yaml
services:
  vpn:
    image: ghcr.io/knaeckebrothero/vpn-sidecar:latest
    container_name: vpn-sidecar
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/ppp
    environment:
      VPN_HOST: ${VPN_HOST}
      VPN_USER: ${VPN_USER}
      VPN_PASS: ${VPN_PASS}
      VPN_TRUSTED_CERT: ${VPN_TRUSTED_CERT}
      SOCKS_PORT: 1080
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- --timeout=3 http://127.0.0.1:8081/ | grep -q '^healthy:'"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  app:
    image: your-app:latest
    environment:
      HTTPS_PROXY: socks5h://vpn:1080
      HTTP_PROXY: socks5h://vpn:1080
    depends_on:
      vpn:
        condition: service_healthy
```

Note: use `socks5h://` (not `socks5://`) so DNS lookups also go through the proxy.

### Kubernetes

Run it as a sidecar in the same pod as your workload. The pod needs a `NET_ADMIN` capability and host device access to `/dev/ppp`. Minimal snippet:

```yaml
containers:
  - name: vpn
    image: ghcr.io/knaeckebrothero/vpn-sidecar:latest
    securityContext:
      capabilities:
        add: ["NET_ADMIN"]
    env:
      - { name: VPN_HOST, valueFrom: { secretKeyRef: { name: vpn, key: host } } }
      - { name: VPN_USER, valueFrom: { secretKeyRef: { name: vpn, key: user } } }
      - { name: VPN_PASS, valueFrom: { secretKeyRef: { name: vpn, key: pass } } }
      - { name: VPN_TRUSTED_CERT, valueFrom: { secretKeyRef: { name: vpn, key: cert } } }
    volumeMounts:
      - { name: dev-ppp, mountPath: /dev/ppp }
    livenessProbe:
      httpGet: { path: /healthz, port: 8081 }
      initialDelaySeconds: 30
      periodSeconds: 20
      failureThreshold: 3
volumes:
  - name: dev-ppp
    hostPath: { path: /dev/ppp, type: CharDevice }
```

---

## Health checks

The container serves an HTTP health endpoint on `HEALTH_PORT` (default `8081`, any path). It's designed to catch **stale tunnels** — situations where `openfortivpn` and `microsocks` are both running but traffic isn't actually flowing through `ppp0`. Those are exactly the cases that process-existence checks miss.

The script at `/healthcheck.sh` reads `ppp0` tx/rx byte counters from `/sys/class/net/ppp0/statistics/` and compares against the previous probe. Verdicts:

| Condition | Status | Body prefix |
| --- | --- | --- |
| `ppp0` missing or `openfortivpn`/`microsocks` dead | 503 | `unhealthy: ...` |
| No active SOCKS connections and tx hasn't moved | 200 | `healthy: idle ...` |
| tx and rx both increased | 200 | `healthy: flowing ...` |
| tx increased but rx didn't | 503 | `unhealthy: tunnel stale ...` |

The "idle" branch avoids false positives when nothing is using the proxy — there's no traffic to prove staleness against. As soon as real traffic resumes, the next probe catches a stuck tunnel within one interval.

Recommended probe settings:

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8081 }
  initialDelaySeconds: 30   # give openfortivpn time to bring ppp0 up
  periodSeconds: 20         # probe cadence = staleness detection latency
  timeoutSeconds: 5
  failureThreshold: 3       # 3 x 20s = ~60s of bad traffic before restart
```

When the probe fails, Kubernetes restarts just the sidecar container — the rest of the pod keeps running, and the entrypoint's reconnect loop re-establishes the tunnel on the next start.

---

## Build locally

```bash
# Docker
docker build -t vpn-sidecar ./devops/vpn-sidecar

# Podman
podman build -t vpn-sidecar ./devops/vpn-sidecar
```

The build stage compiles `microsocks` from source (~5 seconds), then the final image pulls `openfortivpn`, `ppp`, and `socat` from Alpine's `edge/testing` repository. Final size: ~15 MB.

---

## Troubleshooting

**`openfortivpn` fails with `Could not open /dev/ppp`**
The `ppp` kernel module isn't loaded or `/dev/ppp` isn't exposed to the container. Run `sudo modprobe ppp_generic` on the host and make sure you pass `--device=/dev/ppp` (Docker/Podman) or mount it via `hostPath` (Kubernetes).

**`ERROR: Gateway certificate validation failed`**
You need a trusted-cert hash or a CA file. See [Finding the trusted-cert hash](#finding-the-trusted-cert-hash).

**Connection succeeds but clients hang**
Your client is probably using `socks5://` and failing DNS resolution for internal hostnames. Use `socks5h://` so the DNS lookup happens inside the VPN.

**Container restarts with exit code 1 immediately**
Usually auth. Check the logs — openfortivpn's error is printed before the reconnection backoff kicks in.

**VPN drops after ~5 hours**
Expected — FortiGate servers commonly enforce a hard session cap. The sidecar detects clean drops (`CONNECTED_SECS >= 60`) and reconnects within ~5 seconds.
