# Model Orchestrator

OpenAI API-compatible gateway that routes `/v1/*` requests to one of several
backend model servers based on the `model` field. Supports chat, completions,
embeddings, rerank, vision, STT, and TTS backends.

Features today: YAML-driven routing, Postgres-backed API keys (per-key rate
limit, model allowlist, optional expiry) with daily usage aggregation, a
Streamlit admin UI for key management + monitoring, nginx TLS termination,
optional per-route proxying (e.g. through a VPN sidecar).

See `roadmap.md` for planned work.

The deployed stack is four containers: **nginx** (TLS, public ports) →
**router** (this app, loopback) + **admin UI** (Streamlit, loopback), both
backed by **Postgres** (loopback). YAML configures server + routes only;
keys and usage live in the database.

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

1. **Auth** — the Authorization header's Bearer token is hashed (SHA-256)
   and looked up in the in-memory key cache, which syncs from Postgres
   every `ROUTER_KEYS_REFRESH_SECONDS` (default 15s — a key created in the
   admin UI works within seconds, and a DB outage degrades to "last known
   keys" instead of taking the gateway down). 401 for unknown, disabled,
   or expired keys. An empty key table rejects everything — never
   anonymous access.
2. **Rate limit** — per-key, sliding 60s window, budget from the key's
   `rate_limit_rpm`. 429 with X-RateLimit-Reset when exhausted.
3. **Route** — the `model` field in the body (or `model` form field for STT)
   picks the route. The route's `type` must match the endpoint (e.g. a
   `chat` route can't be called via `/v1/embeddings`), and if the key has
   an `allowed_models` list (route names; empty = all), the route must be
   in it — 403 otherwise.
4. **Rewrite** — `model` is replaced with the route's
   `backend_model_name`, and any `request_defaults` are merged into the
   payload (client-supplied values win), before forwarding.
5. **Forward** — the body is sent to `route.backend + route.endpoint`. The
   client's `Authorization` header is *not* forwarded; if the route declares
   `backend_api_key_env`, the router injects its own `Authorization: Bearer
   <value>` instead. If the route declares a `proxy:`, the cached httpx
   client for that proxy is used; otherwise the direct client. One cached
   client per distinct proxy URL (connection pools stay warm).
6. **Respond** — non-streaming responses are passed through with
   rate-limit headers; streaming responses are piped line-by-line. Each
   request's terminal outcome is also queued for the Postgres usage
   aggregate (`usage_daily`: one row per day × key × route), which feeds
   the admin UI — the request path never waits on the database.

Every response includes an `X-Request-ID` header (echoed from the incoming
request, or generated). Log lines emitted while handling the request
include the same id plus any known context fields (owner, limit, model,
backend).

Two discovery endpoints don't follow the model-in-body routing above, and
both are open — no auth, no rate limit. `GET /v1/models` lists configured +
discovered models from local metadata. `GET /v1/audio/voices` forwards to a
`tts` backend's own voice listing (a kokoro-fastapi extension) — with one
TTS route it needs no args, otherwise pass `?model=<alias>` to choose among
several.

## Quick start (Podman Quadlet)

Four units: `orchestrator-db` (Postgres), `model-orchestrator` (router,
loopback :8091), `orchestrator-admin-ui` (Streamlit, loopback :8502), and
`orchestrator-nginx` (TLS termination on the public :8090/:8501).

```bash
# 1. Routing config (server + routes only — no keys in YAML)
mkdir -p ~/.config/model-orchestrator/{nginx,certs}
cp config.example.yaml ~/.config/model-orchestrator/config.yaml
$EDITOR ~/.config/model-orchestrator/config.yaml

# 2. Env files (DB credentials, router DSN, UI login) — keep them 600
for f in db router admin-ui; do
  install -m 600 deployment/$f.env.example ~/.config/model-orchestrator/$f.env
  $EDITOR ~/.config/model-orchestrator/$f.env
done

# 3. TLS material for nginx
cp deployment/nginx/orchestrator.conf.template ~/.config/model-orchestrator/nginx/
cp /path/to/server.crt ~/.config/model-orchestrator/certs/server.crt
install -m 600 /path/to/server.key ~/.config/model-orchestrator/certs/server.key

# 4. Install + start the units
cp deployment/orchestrator-db.container deployment/orchestrator-db.volume \
   deployment/model-orchestrator.container \
   deployment/admin-ui.container deployment/nginx.container \
   ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start orchestrator-db model-orchestrator \
   orchestrator-admin-ui orchestrator-nginx

# 5. Smoke test
curl -k https://localhost:8090/health     # API through nginx
curl -k https://localhost:8501/_stcore/health   # admin UI through nginx
```

First boot: the router creates the DB schema; the key table starts empty
(all API requests get 401), so log into the admin UI and create the first
key. Configuration reference: `config.example.yaml` (server, routes).

## Quick start (Kubernetes)

Manifests live under `deployment/kubernetes/`. The ConfigMap holds the
routing config; the Secret holds the Postgres DSN (`ROUTER_DATABASE_URL`).
A reachable Postgres is required — bring your own (the manifests don't
include one), e.g. a cluster service or CNPG; the router creates its own
tables.

```bash
# 1. Namespace + non-secret config
kubectl apply -f deployment/kubernetes/namespace.yaml
$EDITOR deployment/kubernetes/configmap.yaml       # paste your routes
kubectl apply -f deployment/kubernetes/configmap.yaml

# 2. Postgres DSN (Secret) — copy the example, fill in the real DSN, apply.
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
| `ROUTER_DATABASE_URL` | *(unset)* | **Required.** Postgres DSN for API keys + usage. The router refuses to start without it. |
| `ROUTER_KEYS_REFRESH_SECONDS` | `15` | How often the in-memory key cache re-syncs from Postgres. Upper bound on how long a UI change (new key, disable, limit edit) takes to apply. |
| `ROUTER_HOST` | *(unset)* | Bind address; overrides `server.host` from the config. Used to pin the router to `127.0.0.1` behind the nginx TLS terminator. |
| `ROUTER_PORT` | `8090` | TCP port to listen on. Overrides `server.port` from the config. |
| `ROUTER_LOG_LEVEL` | `INFO` | Log level for the `model-orchestrator` logger. Set to `DEBUG` to get request/response body previews. |
| `ROUTER_UNHEALTHY_SECONDS` | `30` | How long a backend stays flagged unhealthy after a connection error or 5xx before being considered for round-robin again. |

### Top-level YAML keys

**`server`** — listener config (`ROUTER_HOST` / `ROUTER_PORT` env vars win).

| Field | Type | Purpose |
| --- | --- | --- |
| `host` | string | Bind address (default `0.0.0.0`). |
| `port` | int | Port (default `8090`). |

API keys are **not** configured in YAML — see *API keys & usage* below.

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
| `backend_api_key_env` | string | Optional. Name of an env var holding a credential for the upstream. When set, the router injects `Authorization: Bearer <value>` on outbound requests to this route (overriding the default behavior of forwarding no auth). Resolved once at startup; if the env var is unset the route still loads but logs a warning. Keys belong in env (Secret / Quadlet env file), never in the YAML. |
| `request_defaults` | map | Optional. Key/value pairs shallow-merged into the outbound JSON payload for this route before forwarding; values the client sent always win (dict values like `chat_template_kwargs` merge per-key). For backend quirks that need a per-request field with no server-side default — e.g. Gemma 4 reasoning needs `skip_special_tokens: false` to keep `reasoning` separate from `content`. No-op for file-upload routes (STT). |

## API keys & usage (Postgres + admin UI)

Keys live in the `api_keys` table, created by the router on startup and
managed through the Streamlit admin UI (`admin_ui/`,
`ghcr.io/knaeckebrothero/model-orchestrator-admin-ui`). Each key carries:

| Column | Meaning |
| --- | --- |
| `key_hash` / `key_prefix` | SHA-256 of the full key + first 12 chars for display. The plaintext key is shown exactly once, at creation — it cannot be recovered, only replaced. |
| `name`, `owner` | Free-form labels; `owner` shows up in logs and usage. |
| `rate_limit_rpm` | Per-key requests-per-minute budget (sliding 60s window). |
| `allowed_models` | Optional array of **route names** (one entry covers all aliases of that route). NULL/empty = all models. Violations get 403. |
| `enabled`, `expires_at` | Disabled or expired keys get 401. Expiry is optional. |

The router mirrors its configured routes into the `routes` table so the UI
can offer a picker for `allowed_models` without parsing the YAML.

Usage lands in `usage_daily` — request + error counts per day × key ×
route, upserted by a background writer (the request path never waits on
Postgres). Deleting a key keeps its usage history (no FK, owner snapshot
per row). The UI's Usage tab shows totals, a stacked per-day chart, and
breakdowns by owner / key / route over a date range.

**Migrating existing YAML keys**: keys keep working only if their hashes
land in the table. One insert per key, preserving the value clients
already have:

```sql
INSERT INTO api_keys (key_hash, key_prefix, name, owner, rate_limit_rpm)
VALUES (encode(sha256('sk-the-existing-key'::bytea), 'hex'),
        substr('sk-the-existing-key', 1, 12), 'Migrated key', 'someowner', 100);
```

## TLS termination (nginx)

The router and the UI speak plain HTTP and are pinned to loopback; the
nginx unit owns the public ports and terminates TLS for both:

| Public (https) | Proxies to | Service |
| --- | --- | --- |
| `:8090` | `127.0.0.1:8091` | router (OpenAI-compatible API) |
| `:8501` | `127.0.0.1:8502` | admin UI (websocket upgrade enabled) |

The config is an nginx *template* (`deployment/nginx/orchestrator.conf.template`)
— the official image envsubsts `${ROUTER_UPSTREAM}` / `${UI_UPSTREAM}` at
startup, so the same file serves the Quadlet (loopback upstreams) and
compose (service-name upstreams) layouts. SSE streaming has
`proxy_buffering off` and long read timeouts; don't drop those when
editing. Certificates mount at `/etc/nginx/certs/server.{crt,key}`.

Heads-up for IP-only certificates: modern clients require a
`subjectAltName` (CN-only matching is widely rejected, especially for IP
hosts). If verification fails against a CA-issued cert, ask for a reissue
with `SAN: IP:<address>`.

## Local development (docker compose)

`docker-compose.yaml` runs the full stack (Postgres + router + admin UI +
nginx) on a bridge network — see the header comment for the one-liner that
generates self-signed dev certs into `./certs` (gitignored). The example
config's backends don't exist in that network; the stack is for exercising
auth, key management, and usage recording, not model inference.

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
  model, latencies; resets on restart). Requires a valid API key.
  Prometheus-formatted output is planned for a later phase (see
  `roadmap.md`).
- **`usage_daily`** — persistent request/error counts per day × key ×
  route in Postgres, browsable in the admin UI's Usage tab.

