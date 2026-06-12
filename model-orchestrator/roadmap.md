# AI Model Router — Roadmap

Living document for planned work on the router. Items near the top are concrete
next steps; items further down are larger ideas that need more scoping before
they become actionable.

## Current state (baseline, 2026-06-12)

- FastAPI pass-through proxy (`main.py` + `store.py`): chat, completions,
  embeddings, rerank, vision, STT, TTS.
- YAML-driven routing (`routes:` mapping model names → backend URL + endpoint),
  union'd with backend `/v1/models` auto-discovery; load-balanced backend
  pools with health flagging + failover.
- **Postgres-backed API keys** (hashed, per-key rpm limit, per-key model
  allowlist, optional expiry) — YAML key/tier config is fully removed.
  Daily usage aggregation (`usage_daily`) per key × route.
- **Streamlit admin UI** (`admin_ui/`) for key CRUD + usage monitoring.
- **nginx TLS termination** in front of router (public :8090) and UI
  (public :8501); router/UI/Postgres are loopback-only.
- Structured logging (request_id, owner, limit, model, backend), OpenAI-shaped
  error envelopes.
- Deployed via Podman Quadlet (`deployment/*.container`, `*.volume`); k8s
  manifests mirror the layout (BYO Postgres). CI builds both images to GHCR.

## Near-term

### 1. Get the university TLS certs reissued

Deployed 2026-06-12 and measured: `curl` accepts the chain (legacy CN
fallback), but **no Python client can verify it** —

- the leaf (`CN=10.18.2.105`) is an **X.509 v1** certificate — the
  pre-extensions format, so it *cannot* carry a `subjectAltName` (typical
  of `openssl x509 -req` without `-extfile`); OpenSSL never falls back to
  CN for IP hosts → "IP address mismatch" on every Python;
- the CA cert lacks a `keyUsage` extension, which Python ≥ 3.13 rejects
  outright (`VERIFY_X509_STRICT` default).

Ask the cert issuer (jfink@fra-uas.de per the cert subject) for: leaf
reissued with `subjectAltName = IP:10.18.2.105`, and the CA cert re-signed
with `keyUsage = critical, keyCertSign, cRLSign` (same keypair keeps the
existing trust imports valid). Until then Python users need
`verify=False` or a custom SSL context.

### 2. Prometheus + Grafana

Observability beyond the admin UI's daily aggregates.

- Add `prometheus_client` to `requirements.txt`.
- Expose `/metrics` in Prometheus format (separate port or config-gated so
  it isn't on the public listener by default).
- Metrics to export:
  - `router_requests_total{key_owner, model, backend, status}` — counter.
  - `router_request_duration_seconds{model, backend}` — histogram.
  - `router_rate_limit_rejects_total{key_owner}` — counter.
  - `router_backend_errors_total{backend, kind}` — counter (timeout, 5xx,
    connection).
  - `router_backend_up{backend}` — gauge (from the load-balancer health
    flags).
- Ship a starter Grafana dashboard JSON under `deployment/grafana/` with
  request rate, p95 latency per model, rate-limit reject rate, and backend
  error rate panels.

## Later

- **Split `main.py`** into a small module layout if it keeps growing
  (currently ~1.3k lines + `store.py`; revisit past ~1.5k).
- **Shared rate-limit store** — the limiter is in-memory, so `replicas > 1`
  multiplies budgets. Move the window to Postgres/Redis if horizontal
  scaling ever becomes a requirement; until then pin one replica.
- **Token-level usage accounting** — `usage_daily` counts requests, not
  tokens. Parsing `usage` from response bodies is easy for non-streaming;
  streaming only carries usage when the client opts into
  `stream_options.include_usage`, and the proxy deliberately never modifies
  payloads — needs a design pass.
- **Go rewrite** — revisit only if Python starts being the bottleneck for
  streaming / concurrency. Deferred for now; the async httpx client is
  probably fine for our scale.

## Done (kept for context)

- **2026-06-12: full stack live on the university server** under the
  recreated `routerprod` account (the old UID-985 one was deleted first):
  orchestrator-db on loopback :5433 (5432 belongs to fessi-postgres),
  router loopback :8091, admin UI loopback :8502, nginx TLS on public
  :8090/:8501 (firewall opened for 8501). llmprod's old router quadlet
  retired to `~/quadlet-retired/`; YAML keys are dead, keys live in
  Postgres only. Verified end-to-end over HTTPS: chat, embeddings,
  TTS→STT roundtrip, 401s for old/no keys, usage_daily rows, plain-HTTP
  → 400. Quadlet gotcha fixed along the way: `DriverOpts` is not a valid
  `[Volume]` key (generator drops the unit) — use `VolumeName`.
- VPN sidecar wiring (SOCKS5 + TCP-forward patterns, multi-tunnel support).
- Kubernetes manifest set under `deployment/kubernetes/`.
- Cleanup: structured logging, OpenAI error envelopes, handler docstrings.
- Load balancing: multi-backend routes, round-robin, unhealthy flagging,
  non-streaming failover.
- Admin UI, per-key model ACLs, persistent key store — landed as the
  Postgres + Streamlit stack (this iteration went straight to Postgres
  instead of SQLite, and Streamlit instead of FastAPI+HTMX).
