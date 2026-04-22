# Model Orchestrator

OpenAI API-compatible gateway that routes `/v1/*` requests to one of several
backend model servers based on the `model` field. Supports chat, completions,
embeddings, rerank, vision, STT, and TTS backends.

Features today: YAML-driven routing, API keys with per-tier rate limiting,
optional per-route proxying (e.g. through a VPN sidecar).

See `roadmap.md` for planned work.

## Architecture

```
┌────────┐   Bearer <key>   ┌────────────────────────────────┐   ┌──────────────┐
│ client │ ───────────────▶ │          model-orchestrator    │ ─▶│ backend A    │
└────────┘                  │ ┌────────────────────────────┐ │   │ (e.g. vLLM)  │
                            │ │ auth → rate limit → route  │ │   └──────────────┘
                            │ │   → rewrite model field    │ │   ┌──────────────┐
                            │ │   → forward (opt. via      │ │ ─▶│ backend B    │
                            │ │     SOCKS5/HTTP proxy)     │ │   │ (e.g. piper) │
                            │ └────────────────────────────┘ │   └──────────────┘
                            └────────────────────────────────┘
```

**Request lifecycle** for a typical `/v1/*` call:

1. **Auth** — the Authorization header's Bearer token must match a key in
   the config; 401 otherwise. The key's `tier` and `owner` are attached to
   the request context.
2. **Rate limit** — per-key, sliding 60s window, budget from the tier. 429
   with X-RateLimit-Reset when exhausted.
3. **Route** — the `model` field in the body (or `model` form field for STT)
   picks the route. The route's `type` must match the endpoint (e.g. a
   `chat` route can't be called via `/v1/embeddings`).
4. **Rewrite** — `model` is replaced with the route's
   `backend_model_name` before forwarding.
5. **Forward** — the body is sent to `route.backend + route.endpoint`. If
   the route declares a `proxy:`, the cached httpx client for that proxy is
   used; otherwise the direct client. One cached client per distinct proxy
   URL (connection pools stay warm).
6. **Respond** — non-streaming responses are passed through with
   rate-limit headers; streaming responses are piped line-by-line.

Every response includes an `X-Request-ID` header (echoed from the incoming
request, or generated). Log lines emitted while handling the request
include the same id plus any known context fields (owner, tier, model,
backend).

## Quick start (Podman Quadlet)

```bash
# 1. Put your config in place
cp config.example.yaml ~/.config/model-orchestrator/config.yaml
$EDITOR ~/.config/model-orchestrator/config.yaml

# 2. Build the image
podman build -t localhost/model-orchestrator:latest .

# 3. Install + start the unit
cp deployment/model-orchestrator.container \
   deployment/model-orchestrator.volume \
   ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start model-orchestrator

# 4. Smoke test
curl http://localhost:8090/health
```

Configuration reference: `config.example.yaml` (server port, tiers, API keys,
routes).

## Quick start (Kubernetes)

Manifests live under `deployment/kubernetes/`. They mirror the Quadlet layout
but split the API keys into a Secret (the ConfigMap holds the rest of the
config). The router merges them at startup via `ROUTER_KEYS_CONFIG`.

```bash
# 1. Namespace + non-secret config
kubectl apply -f deployment/kubernetes/namespace.yaml
$EDITOR deployment/kubernetes/configmap.yaml       # paste your routes/tiers
kubectl apply -f deployment/kubernetes/configmap.yaml

# 2. API keys (Secret) — copy the example, fill in real keys, apply.
cp deployment/kubernetes/secret.yaml.example /tmp/mo-secret.yaml
$EDITOR /tmp/mo-secret.yaml
kubectl apply -f /tmp/mo-secret.yaml && rm /tmp/mo-secret.yaml

# 3. (Optional) VPN sidecar credentials — only if you have routes with proxy:
cp deployment/kubernetes/vpn-secret.yaml.example /tmp/mo-vpn.yaml
$EDITOR /tmp/mo-vpn.yaml
kubectl apply -f /tmp/mo-vpn.yaml && rm /tmp/mo-vpn.yaml

# 4. Deployment + service
kubectl apply -f deployment/kubernetes/service.yaml
kubectl apply -f deployment/kubernetes/deployment.yaml

# 5. (Optional) Ingress
cp deployment/kubernetes/ingress.yaml.example /tmp/mo-ingress.yaml
$EDITOR /tmp/mo-ingress.yaml                        # host + tls secret
kubectl apply -f /tmp/mo-ingress.yaml

# Smoke test
kubectl -n model-orchestrator port-forward svc/model-orchestrator 8090:8090 &
curl http://localhost:8090/health
```

**No VPN-routed backends?** Delete the `vpn-sidecar` container block from
`deployment.yaml` (and skip step 3). The router works fine without it; only
routes that set `proxy: socks5h://...` need the sidecar in the pod.

**Caveat — single replica.** `deployment.yaml` pins `replicas: 1` because
the rate limiter is in-memory; running multiple replicas gives per-pod
counters. A shared-store replacement is on the roadmap.

## Routing backends through the VPN sidecar

Some model servers only exist on an internal network. The router can dial
them through the [VPN sidecar](../devops/vpn-sidecar/README.md)
(`ghcr.io/knaeckebrothero/vpn-sidecar`) so the VPN stays scoped to the
sidecar's network namespace instead of the whole host.

Two integration patterns are supported.

### Pattern A — SOCKS5 proxy (recommended)

One sidecar serves many routes. Use when you have several internal backends
or want the flexibility to add more later.

1. Create the credentials file (not committed):

   ```bash
   mkdir -p ~/.config/model-orchestrator
   install -m 600 /dev/null ~/.config/model-orchestrator/vpn.env
   $EDITOR ~/.config/model-orchestrator/vpn.env
   ```

   Required keys: `VPN_HOST`, `VPN_USER`, `VPN_PASS`, `VPN_TRUSTED_CERT`
   (see the sidecar README for how to find the cert hash). Optional:
   `VPN_PORT`, `VPN_REALM`, `SOCKS_PORT`, `HEALTH_PORT` (defaults to
   `8081`; see *Sidecar health checks* below).

2. Install and start the sidecar Quadlet:

   ```bash
   cp deployment/vpn-sidecar.container ~/.config/containers/systemd/
   systemctl --user daemon-reload
   systemctl --user start vpn-sidecar
   ```

3. Add `proxy:` to any route that should go through the tunnel:

   ```yaml
   - name: "Internal-LLM"
     type: "chat"
     backend: "http://llm-internal.corp.example:8000"
     endpoint: "/v1/chat/completions"
     backend_model_name: "internal-llm"
     proxy: "socks5h://127.0.0.1:1080"
     models:
       - "internal-llm"
     owned_by: "internal"
   ```

   Prefer `socks5h://` over `socks5://` so DNS resolution also goes through
   the tunnel. Restart the router to pick up the new route.

The router lazily creates one cached httpx client per distinct proxy URL on
startup, so all routes sharing a proxy share a connection pool.

#### Multiple tunnels (two or more VPN networks)

Run one sidecar per network on distinct loopback ports and point each route
at the tunnel it belongs to. Quadlet: the repo ships `vpn-sidecar.container`
(port 1080) and a sibling `vpn-sidecar-b.container` (port 1081) — copy both
into `~/.config/containers/systemd/`, create a second credentials file at
`~/.config/model-orchestrator/vpn-b.env` with `SOCKS_PORT=1081` **and
`HEALTH_PORT=8082`** set inside, and start both units. Kubernetes:
uncomment the `vpn-sidecar-b` container block in
`deployment/kubernetes/deployment.yaml` and create a second Secret
(`model-orchestrator-vpn-b`) with `SOCKS_PORT=1081` and `HEALTH_PORT=8082`
set. Routes then set `proxy: socks5h://127.0.0.1:1080` or `:1081` as
appropriate. Connection pools stay separate per proxy URL, so the tunnels
don't fight over keep-alive slots. Both sidecars share the pod's network
namespace, so SOCKS *and* health ports must all be distinct.

#### Sidecar health checks

The sidecar exposes an HTTP health endpoint on `HEALTH_PORT` (default
`8081`) that detects **stale tunnels** — cases where `openfortivpn` is
still running but traffic silently stops flowing through `ppp0`. The
endpoint compares tx/rx byte counters between probes: if bytes were sent
in but nothing came back, it returns `503`. When idle it returns `200`
(no traffic to test against, so no false positives).

In Kubernetes the deployment wires this as a `livenessProbe` on the
sidecar container — a failing probe restarts just that container while
leaving the router running. See the
[sidecar README](../devops/vpn-sidecar/README.md#health-checks) for the
full verdict table and probe-tuning guidance.

### Pattern B — TCP forward

No router code change needed. Use when you have a single internal endpoint
and want to present it as a local `host:port`.

1. Start a sidecar with `FORWARD_PORT` / `FORWARD_TARGET` set (see the
   sidecar README). Publish the forward port on the host.

2. Point the route's `backend:` at the forwarded local port:

   ```yaml
   - name: "Internal-API"
     type: "chat"
     backend: "http://127.0.0.1:8088"   # sidecar's FORWARD_PORT
     endpoint: "/v1/chat/completions"
     # ...
   ```

Trade-off: simpler, but requires one sidecar per internal endpoint.

## Configuration reference

The canonical example lives in `config.example.yaml`. This section summarises
the schema.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROUTER_CONFIG` | `config.yaml` | Path to the main YAML config. |
| `ROUTER_KEYS_CONFIG` | *(unset)* | Optional second YAML file whose `api_keys:` list is merged into the main config. Intended for k8s Secret-based split. |
| `ROUTER_PORT` | `8090` | TCP port to listen on. Overrides `server.port` from the config. |
| `ROUTER_LOG_LEVEL` | `INFO` | Log level for the `model-orchestrator` logger. Set to `DEBUG` to get request/response body previews. |
| `ROUTER_UNHEALTHY_SECONDS` | `30` | How long a backend stays flagged unhealthy after a connection error or 5xx before being considered for round-robin again. |
| `ROUTER_ALLOW_ANONYMOUS` | *(unset)* | Dev escape hatch. Set to `1` to start with no `api_keys:` configured. Without it the router refuses to boot on an empty key list, since empty = auth bypass. |

### Top-level YAML keys

**`server`** — listener config.

| Field | Type | Purpose |
| --- | --- | --- |
| `host` | string | Bind address (default `0.0.0.0`). |
| `port` | int | Port (default `8090`). |

**`tiers`** — a map of tier name → limit config. Keys are arbitrary; names
are referenced by `api_keys[*].tier`.

| Field | Type | Purpose |
| --- | --- | --- |
| `requests_per_minute` | int | Sliding 60s window budget per key in this tier. |
| `burst` | int | Advisory only; not currently enforced. |
| `priority` | int | Advisory only; not currently consumed. |
| `description` | string | Free-form; appears nowhere programmatic. |

**`api_keys`** — a list of key entries.

| Field | Type | Purpose |
| --- | --- | --- |
| `key` | string | The Bearer token the client sends. |
| `owner` | string | Free-form label; shown in logs and `/metrics`. |
| `tier` | string | Must match a tier name in `tiers:`. Unknown tiers fall back to `standard`. |
| `enabled` | bool | Disabled keys get 401. |
| `name` | string | Free-form; not currently surfaced. |

**`routes`** — a list of routing entries. The first route that claims a
given model name (via its `models:` list) wins.

| Field | Type | Purpose |
| --- | --- | --- |
| `name` | string | Free-form label, used in logs. |
| `type` | enum | One of `chat`, `vision`, `embedding`, `reranker`, `stt`, `tts`. Must match the endpoint the client calls. |
| `backend` | string \| list[string] | Upstream URL (`http://host:port`). Accepts a list for round-robin load balancing — see *Load balancing* below. |
| `endpoint` | string | Path appended to `backend` when forwarding (e.g. `/v1/chat/completions`). |
| `backend_model_name` | string | Substituted into the payload's `model` field before forwarding. Defaults to the client-visible model name. |
| `models` | list[string] | Client-visible model names this route claims. |
| `owned_by` | string | Shown in `/v1/models` responses. |
| `proxy` | string | Optional. HTTP/SOCKS proxy URL (`socks5h://host:port`) for all requests going to this backend. See *Routing backends through the VPN sidecar*. |
| `health_path` | string \| null | Optional. Path used by the router's `/health` endpoint to probe this backend. Omit for the default (`/health`). Set to `null` to skip probing entirely — needed for backends that don't expose a health endpoint (e.g. Kokoro TTS, stock Whisper). |

## Load balancing

A route's `backend:` can be either a single URL string or a list of URLs.
With a list, the router round-robins requests across the pool per route
(per-route counter, shared across all model aliases that point at the same
route).

- **Health tracking** — a backend that returns a 5xx or fails to connect /
  times out is flagged unhealthy for `ROUTER_UNHEALTHY_SECONDS` (default
  30) and skipped during selection. If every backend in a route is flagged
  at the same time, the router falls back to the full set rather than
  hard-failing — transient marks shouldn't freeze the route.
- **Retry/failover** — non-streaming requests get one retry against a
  different backend on connection error, timeout, or 5xx. Client-errors
  (4xx) don't trigger failover — the backend is telling us the request
  itself is wrong.
- **Streaming requests** pick one backend and don't failover — once bytes
  start flowing to the client, there's no safe way to retry. The chosen
  backend is still marked unhealthy on connection failure so subsequent
  requests route elsewhere.

Example route with two backends:

```yaml
- name: "Chat-Pool"
  type: "chat"
  backend:
    - "http://llm-1.internal:8000"
    - "http://llm-2.internal:8000"
  endpoint: "/v1/chat/completions"
  models: ["chat-pool"]
```

## Observability

- **Request IDs** — every response carries `X-Request-ID`. The router honours
  an incoming `X-Request-ID` header if the caller sets one, otherwise
  generates a short id. Logs emitted during the request include the id.
- **Log level** — set `ROUTER_LOG_LEVEL=DEBUG` to include a preview of
  outgoing payloads and response status/latency per backend call.
- **Error shape** — all errors follow OpenAI's envelope:
  `{"error": {"message": ..., "type": ..., "code": ...}}`. `type` is
  derived from the status code (`authentication_error`, `rate_limit_error`,
  `invalid_request_error`, `api_error`).
- **`/metrics`** — returns an in-memory JSON view (request counts per key/
  model, latencies). Prometheus-formatted output is planned for a later
  phase (see `roadmap.md`).

