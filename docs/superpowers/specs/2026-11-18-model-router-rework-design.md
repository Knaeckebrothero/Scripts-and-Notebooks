# Model router rework: core design

**Date:** 2026-11-18
**Status:** implemented on 2026-11-19 (section 13 lists where the code went beyond this text) and merged to `main` the same day. Released as `3.0.0` on 2026-11-22 (tag `model-orchestrator-v3.0.0`). Rollout (section 10): the pilot site runs version 3 since 2026-11-19, the other site since the release.
**Scope:** the router service in `model-orchestrator/`: proxying, keys, limits, usage, health, metrics, tests and CI. The new admin website with Keycloak login was meant to be a separate project after this one; it was built on the same branch, and its decisions are recorded in [2026-11-19-admin-website-design.md](2026-11-19-admin-website-design.md).

Site-specific facts (hosts, ports, accounts, tunnel details, rollout commands) are deliberately not in this file, because this repository is public. They live in the private infra notes in `docs_uni_infra/` (not in git): `05-model-router.md`, `07-homelab-integration.md` and `15-router-rework.md`.

---

## 1. Why

The router is the single door to every self-hosted model at two sites (a university GPU host and a homelab cluster). It was built quickly and it shows:

- One 1,300-line module (`main.py`) that loads config and builds state at import time, so nothing can be tested in isolation. There are no tests at all.
- CI builds and publishes an image on every push, including documentation-only changes, with unpinned dependencies and no checks.
- Known defects that clients hit: errors on streaming requests are swallowed, backend errors are rewritten to a generic 503, speech-to-text and rerank drop request fields, dead backends stay advertised, abandoned requests keep running on the GPU.
- No token accounting, no Prometheus metrics, no per-request record. Questions such as "why did this key get 38 % errors last month" cannot be answered.
- The two sites keep separate copies of the route list, synced by hand. They have drifted more than once.

More GPU machines are arriving, so the router will soon front more backends on several hosts. It has to be something we can change with confidence.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Keep the client contract (section 5) exactly, and prove it with an end-to-end test suite.
2. Fix the known defects (section 6 lists every change a client can observe).
3. Add: token usage per key and model, a per-key concurrency cap, Prometheus metrics, one structured log line per request, active health checks that drive model availability, validated and reloadable config, an "upstream is another router" mode, database migrations.
4. Make the code maintainable: a package with small modules, no import-time side effects, a dependency lockfile, lint and type checks, unit and end-to-end tests, CI that gates image publishing.
5. Roll out without a big-bang cutover: every step is a normal image upgrade.

**Non-goals (not in this project)**

- The new admin website and Keycloak login. The existing Streamlit admin UI must keep working unchanged until that project replaces it.
- More than one router replica, or a shared store for limiter and health state.
- Load-aware balancing (picking the least busy replica).
- Token budgets or quotas. Accounting comes first; limits on tokens can follow once there are numbers.
- GPU scheduling, planned availability windows, model swapping.
- New API endpoints (`/v1/completions`, `/v1/responses`, images). The design should make adding a pass-through endpoint a small change, but none is added now.
- Locking down direct access to model servers. That is a deployment task at each site.
- Database backups (a deployment task; listed under follow-ups).

**Anti-goals (would make this a failure even if it works)**

- Turning the router into a translation layer. It forwards bodies; it does not reshape them.
- Any change to what clients send or receive beyond the list in section 6.
- A cutover that replaces the running router in one step, or that needs both sites to change at the same time.
- An image tag that production tracks moving on an untested or documentation-only commit.

## 3. Constraints

- **Change freeze at the university site** from 2026-11-23 to 2027-01-01 (long batch runs depend on the host). Nothing on that host changes in this window, including what its auto-update pulls. The homelab is the pilot site.
- **Two deployment targets, one image:** Kubernetes (homelab now, a university cluster later) and rootless Podman with systemd Quadlet units (university today).
- **Single replica, single asyncio process.** Limiter and health state stay in memory.
- **Postgres 16 and 17** are both in use. Schema changes are additive only. Both live databases must be adopted in place, with no manual step.
- **Existing API keys keep working.** The key hash (unsalted SHA-256 hex of the full token) and the `api_keys` columns in use do not change.
- **Both live route configs must load unchanged** with the new code.
- **Public repository:** no internal addresses, account names or credentials in code, tests, fixtures or docs.

## 4. Approaches considered

| Approach | What it is | Trade-off | Verdict |
|---|---|---|---|
| Adopt an existing gateway (LiteLLM, agentgateway) | Replace our router with an off-the-shelf engine, possibly with a thin portal on top | Brings keys, budgets and token tracking for free. But these are translation layers: LiteLLM renames vLLM's `reasoning` field to `reasoning_content`, which breaks clients. Keycloak login is paid in LiteLLM beyond 5 users. LiteLLM had at least 10 security advisories in 2026. Health handling does not shield a slow-starting single backend. | Rejected |
| Rewrite in Go | New implementation with the standard reverse proxy | Clean base and natural proxy semantics. But the load is small, cancellation and dependency pinning are cheap in Python too, and the one hard reason (an ARM build) fell away when it was decided the router does not run on the ARM machine. Cost: reaching parity first with no visible gain, then replacing a working router at both sites. | Rejected |
| **Restructure the Python router in place** | Keep the code and the stack. Put an end-to-end contract suite around the current behaviour first, split the module, then fix and extend in small releases. | Lowest risk: each step ships on its own and the university upgrade is an image bump. Stays single-process Python, which is enough for this traffic. | **Chosen** |

The router is a plain pass-through in front of servers that already speak the OpenAI API. That is why new vLLM features work without router changes, and it is the property to protect.

## 5. The client contract (must not change)

- **Endpoints:** `POST /v1/chat/completions` (streaming and non-streaming, chat and vision routes), `POST /v1/embeddings`, `POST /v1/rerank`, `POST /v1/audio/transcriptions` (multipart), `POST /v1/audio/speech`, `GET /v1/audio/voices[?model=]`, `GET /v1/models`, `GET /v1/models/{id}`, `GET /livez`, `GET /health`.
- **Model ids:** every configured alias, plus every id a backend reports in its own `/v1/models` (today only the first backend of a pool is asked; pools are assumed to serve the same model). An alias hit rewrites `model` to the route's `backend_model_name`; a discovered id is forwarded as sent. Aliases beat discovered ids. Among discovered ids the first route in config order wins.
- **Bodies are forwarded untouched** apart from the `model` rewrite and the route's `request_defaults`. Unknown fields pass through in both directions. In particular the `reasoning` and `delta.reasoning` fields reach the client as the backend sent them.
- **`request_defaults` semantics:** the client's value always wins; dict values are shallow-merged key by key.
- **Streaming:** server-sent events are relayed byte for byte, including the blank-line terminators.
- **Auth:** `Authorization: Bearer <key>`. Missing, unknown, disabled and expired keys all get 401 with the same message. An empty key table denies everything. A key's `allowed_models` holds route names; a violation is 403.
- **Rate limit:** per-key requests per minute over a sliding 60 s window, 429 when exceeded. `X-RateLimit-Limit/Remaining/Reset` headers on successful responses, on streams and on the 429 itself.
- **Route type check:** a model used on the wrong endpoint gets 400; an unknown model gets 404.
- **Errors** the router raises on purpose use the OpenAI envelope `{"error": {"message", "type"}}`. No `code` is set today.
- **Headers:** `X-Request-ID` on every response the router produces on purpose, echoed if the client supplied one. The client's key is never forwarded to a backend; a route's own backend key is sent instead when configured.
- **Limits clients rely on:** 600 s read timeout for chat and vision (env `CHAT_READ_TIMEOUT_S`), 300 s for speech-to-text, 60 s for embeddings, rerank and text-to-speech. Request bodies up to 200 MB pass; today that limit is set by the proxy in front, the router itself has none.
- **Degraded mode:** if the database goes away at runtime, requests keep authenticating from the cached key set.
- **Environment variables and the YAML route schema** stay valid as they are. The listen address is `ROUTER_HOST`, else `server.host`, else `0.0.0.0`. The port is `ROUTER_PORT`, else 8090; `server.port` is not read today and stays ignored (a warning is logged if it differs from the effective port).

## 6. Intentional client-visible changes

Everything else must behave as before. Each item gets a test that documents the old and the new behaviour.

| ID | Today | New |
|---|---|---|
| C1 | Streaming requests always answer HTTP 200. A backend error status produces an empty stream that ends cleanly, so the client cannot tell anything failed (a bug: the code reads an unread response). A connect error or timeout produces an error event. A backend that drops the connection mid-stream ends the stream cleanly after the events already sent, with no error event. | If the failure happens before the first byte, the client gets the real HTTP status and a normal JSON error. A failure after streaming has started, including a dropped connection, is reported as an error event in the stream. |
| C2 | A backend 5xx becomes `503 All backends unavailable: …`. | The backend's own status and message are returned. |
| C3 | A read timeout becomes 503, after a second attempt on another backend. | 504 with code `backend_timeout`, and no second attempt. |
| C4 | Backend error bodies are wrapped as a string: `"Backend error: {raw JSON}"`. | The envelope's `message` is the backend's own message, extracted from the backend body. Header `X-Router-Error-Source: backend` or `router` says who produced the error. |
| C5 | Rerank always drops `top_n`. | `top_n` is forwarded when it is a number. It is dropped only when null. |
| C6 | Speech-to-text forwards only `file` and `model`. A missing file is a 500. A missing `model` defaults to a hard-coded name. | All form fields are forwarded (repeated fields included). A missing file is a 400. A missing `model` uses the first speech-to-text route. |
| C7 | A model whose backends are all down is still listed and fails after a connect timeout. A discovered id returns 404 while its backend is down. | It is not listed in `/v1/models`, and requests get an immediate 503 with code `model_unavailable` and `Retry-After`. A discovered id that was seen before returns that 503, not 404. |
| C8 | `/health` lists backend URLs and the number of keys to anyone. | Public `/health` reports overall status, version, database status and up/down per route name. The detailed view moves to the internal port. `/health` no longer probes on every call. |
| C9 | `GET /metrics` on the API port returns JSON counters for all owners to any key holder. | Removed. Prometheus metrics are served on a separate internal port. |
| C10 | `/docs`, `/redoc` and `/openapi.json` are public. | Off unless `ROUTER_EXPOSE_DOCS=1`. |
| C11 | Two routes sharing an alias: the later one silently wins. | Startup (and reload) fails with a clear message. |
| C12 | Unknown paths and wrong methods return FastAPI's `{"detail": …}`. A JSON body that is not an object (for example `[]`) and any unexpected internal error return a plain-text 500 without `X-Request-ID`. | All of these use the OpenAI error envelope and carry `X-Request-ID`. A non-object JSON body is a 400. |
| C13 | – | A key with a concurrency cap gets 429 with code `concurrency_limit_exceeded` when it has too many requests in flight. Keys without a cap are unaffected. |
| C14 | – | `/v1/models` entries gain an `x_router` object: `{"route": <route name>, "type": <route type>}`. |
| C15 | `/v1/audio/voices` calls the backend on every request, without auth. | Still without auth, but served from a short cache (5 minutes per route). |
| C16 | When a non-streaming client disconnects, the backend keeps generating. | The backend call is cancelled. |
| C17 | The connect timeout is 10 s for chat and vision only. Elsewhere it equals the read timeout (30 to 300 s). | 10 s on every endpoint, so a dead backend fails fast everywhere. |
| C18 | The router accepts a body of any size; only the proxy in front limits it. | Bodies over `ROUTER_MAX_BODY_MB` (default 200) get 413. |
| C19 | Error envelopes never carry `code`. `X-RateLimit-*` headers are missing on error responses. | `code` is set where this design names one. The rate-limit headers are on every response once the limiter has run. |
| C20 | `GET /v1/models/{id}` returns 404 for any id containing `/`, which is most discovered ids. | Works for those ids too. |
| C21 | A streaming request gets one backend and no second attempt. | In a pool, a stream gets a second attempt on another backend as long as no byte has reached the client. |
| C22 | A backend that closes the connection without a complete answer (non-streaming) causes a plain-text 500 without `X-Request-ID`. So does a `model` value that is a list or an object (a number gets a 404 today). | The dropped connection counts as a failed attempt; the final answer is 502 with code `backend_disconnected`. A non-string `model` is a 400. Both use the envelope. |

Two notes on the "Today" column. For C7, a discovered id only turns into a 404 after the next discovery run, not at the moment its backend goes down. And all of it was measured against the current code with current library versions; the deployed images were built from unpinned dependencies and may differ in such details.

## 7. Design

### 7.1 Code structure

`model-orchestrator/` becomes an installable package (suggested name `model_orchestrator`) with a `pyproject.toml` and a `uv.lock`. Python 3.13. The module split below is a suggestion; the rule that matters is **no state at import time**: an app factory takes settings and config and returns the app, and all runtime state hangs off one object created in the lifespan. That is what makes unit tests possible.

| Module | Responsibility |
|---|---|
| `settings` | Environment settings (existing names kept) |
| `config` | YAML schema as typed models, validation, reload |
| `registry` | The model table: aliases and discovered ids to routes, availability |
| `health`, `discovery` | Active probes; `/v1/models` discovery for backends and upstream routers |
| `auth`, `limits` | Bearer key to key record; per-minute window and concurrency |
| `proxy`, `sse` | Forwarding (JSON, multipart, streaming), failover, cancellation; SSE event splitting and usage extraction |
| `usage`, `db` | Usage events and the background writer; pool, migrations, key cache |
| `metrics`, `logging`, `errors` | Prometheus, request log, error envelopes |
| `api` | Thin endpoint handlers driven by an endpoint table |

`admin_ui/` is not touched.

### 7.2 Configuration

**Environment.** All existing variables keep their names and defaults. New ones:

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_METRICS_PORT` | 9095 | Internal listener for Prometheus metrics and detailed health. Binds to `ROUTER_METRICS_HOST` (default: same as `ROUTER_HOST`). |
| `ROUTER_HEALTH_INTERVAL` | 10 | Seconds between active probes |
| `ROUTER_HEALTH_TIMEOUT` | 5 | Probe timeout in seconds |
| `ROUTER_HEALTH_FAILS` | 3 | Consecutive failed probes before a backend counts as down |
| `ROUTER_MAX_BODY_MB` | 200 | Larger requests get 413 |
| `ROUTER_LOG_FORMAT` | `text` | `text` or `json` |
| `ROUTER_EXPOSE_DOCS` | off | Serve `/docs`, `/redoc`, `/openapi.json` |
| `ROUTER_CONFIG_POLL_SECONDS` | 10 | How often the config file is checked for changes (0 disables) |

**YAML.** The current schema (`server`, `routes[]` with `name`, `type`, `backend`, `endpoint`, `backend_model_name`, `models`, `owned_by`, `proxy`, `health_path`, `backend_api_key_env`, `request_defaults`) stays valid. Additions, all optional:

- per route: `timeout_s` (overrides the endpoint's default read timeout) and `usage_from_stream` (default true; see 7.6);
- top level: `upstreams[]` (see 7.9).

**Validation.** The config is parsed into typed models. Required per route: `name`, `type`, `backend`, `endpoint`. Unknown keys, a missing required key, an unknown `type`, a duplicate route name and a duplicate alias (C11) are errors with the route name and field in the message. A `backend_api_key_env` that names an unset variable stays a warning, as today. `health_path: null` and an empty string both mean "no probe".

Unknown keys are errors on purpose: a misspelt `request_defaults` that is silently ignored would break reasoning output without any signal. Both live configs were checked against these rules on 2026-11-18 and pass: every route has the four required keys, there are no duplicate aliases, and only the keys listed above are used. Sanitised copies of both (same structure, hosts replaced by placeholders) go into the test fixtures; their sources are named in the private notes.

**Reload.** The router re-reads the file on `SIGHUP` and when its modification time changes. A valid config replaces the route table atomically, re-syncs the `routes` table, creates clients for new proxies, and triggers discovery. An invalid config is rejected: the running config stays, an error is logged, and a metric records the failure. The `server` block only applies at start.

Two guards against a half-written file. A reload is refused when the new config has no routes and no upstreams (an empty config is still allowed at startup, with a warning). And a file change is only acted on once the file's size and modification time have been the same for two consecutive polls.

On Kubernetes a ConfigMap mounted with `subPath` never updates in the container, so in-place reload needs a directory mount. Restart-on-change (the current homelab setup) keeps working either way.

**Check command.** `python -m model_orchestrator check-config <file>` validates a config and exits non-zero on error, so a config repository can test a change before it is deployed.

### 7.3 Model table, health and availability

**Active probes.** Every backend URL of a route with a `health_path` (default `/health`) is probed every `ROUTER_HEALTH_INTERVAL` seconds. The probe sends the route's backend key if it has one. A probe succeeds on any HTTP status below 500 and fails on a connect error, a timeout or a 5xx. (Today's display-only check wants exactly 200. Gating on that would take a working backend offline just because its health path answers 404 or 401.) A backend counts as down after `ROUTER_HEALTH_FAILS` consecutive failures and as up after one success.

`ROUTER_HEALTH_GATING=off` keeps the probes and their reporting but stops them from affecting availability. It is the escape hatch if gating misbehaves at a site that upgrades unattended; the default is on. A busy but working backend must not be taken out; that is why the timeout and threshold are generous. At startup one probe round runs before the listener opens, and its result is taken at face value. A connect failure on a real request triggers an immediate probe.

A route with `health_path: null` has no active probe and always counts as available, as today.

**Passive marks stay, but only steer selection.** A connect error, timeout or 5xx marks a backend for `ROUTER_UNHEALTHY_SECONDS`; within a pool, unmarked backends are preferred, and if all are marked all are used. Passive marks never make a model unavailable. Only active probes do. (Otherwise one request that triggers a backend 500 could take a model offline for everyone.)

**Availability.** A model id is available when its route has at least one backend that is not known-down. `/v1/models` lists available ids only. A request for a known but unavailable id gets 503 `model_unavailable` with `Retry-After: 30` and is not forwarded (C7). `GET /v1/models/{id}` returns 404 for an unknown id and 503 for a known, unavailable one.

**Discovery.** Runs after the startup probe round, whenever a backend goes from down to up, and every `ROUTER_DISCOVERY_INTERVAL` seconds. In a pool, each up backend is asked until one answers. Discovered ids are remembered for as long as their route stays in the config, so a backend that goes down later yields 503, not 404. This replaces the fixed warm-up ladder.

### 7.4 Auth and limits

Semantics are unchanged (section 5). Internally a request carries a key record (id, name, owner, limits, allowlist); the raw token is never used as a dictionary key or kept after hashing.

- **New column** `api_keys.max_concurrency` (nullable integer, NULL means no cap). The Streamlit UI names its columns explicitly, so it is unaffected.
- **Concurrency:** requests in flight per key are counted from admission until the response has been sent or aborted (for streams: until the stream ends). Over the cap is 429 (C13) with `Retry-After: 1`.
- **Order of checks:** auth, model lookup (404), type check (400), allowlist (403), per-minute limit, concurrency cap, availability (503), forward. As today, requests rejected before the limiter do not consume the per-minute budget.

### 7.5 Forwarding

**Endpoint table.** Handlers are driven by one table, so adding a pass-through endpoint later is one entry plus tests:

| Path | Route types | Body | Default read timeout | Stream |
|---|---|---|---|---|
| `/v1/chat/completions` | chat, vision | JSON | `CHAT_READ_TIMEOUT_S` (600 s) | yes |
| `/v1/embeddings` | embedding | JSON | 60 s | no |
| `/v1/rerank` | reranker | JSON | 60 s | no |
| `/v1/audio/transcriptions` | stt | multipart | 300 s | no |
| `/v1/audio/speech` | tts | JSON in, bytes out | 60 s | no |
| `/v1/audio/voices` (GET) | tts | none | 30 s | no |

The connect timeout is 10 s on every endpoint (C17). `X-Request-ID` is now also sent to the backend. Backends are asked for uncompressed responses (`Accept-Encoding: identity`), because stream bytes are relayed as they arrive and a compressing backend would otherwise corrupt the stream.

**Failover.** Up to two attempts on distinct backends of a pool, for streaming requests too as long as no byte has been sent to the client. A second attempt is made after a connect failure or a backend 5xx. It is not made after a read timeout (C3) or a 4xx.

**Error mapping.**

| Situation | Response |
|---|---|
| Backend 4xx | Same status, backend's message (C4), source `backend` |
| Backend 5xx on the last attempt | Same status, backend's message (C2), source `backend` |
| No backend reachable | 503, code `backend_unreachable`, source `router` |
| Backend closes the connection without a complete answer (C22) | 502, code `backend_disconnected`, source `router`. Counts as a failed attempt, so a second backend may be tried. |
| Read timeout | 504, code `backend_timeout`, source `router` |
| Stream breaks after the first byte | An SSE event `data: {"error": {...}}`, then `data: [DONE]`, then the stream ends |

Router-generated messages never contain backend URLs. Message extraction (C4) understands the bodies vLLM and TEI produce (`error.message`, a top-level `message`, an `error` string, `detail`) and falls back to the raw text, truncated.

**Streaming with real status codes (C1).** The router opens the upstream request and waits for its response headers before it answers the client. On an error status it reads the body and returns a normal JSON error. On success it relays the stream and closes the upstream response when the stream ends or the client goes away.

**Client disconnect (C16).** For non-streaming requests the upstream call runs alongside a watcher for the client's disconnect; whichever finishes first wins, and a disconnect cancels the upstream call. For streams, closing the relay closes the upstream. This only helps where the proxies in front pass the disconnect on, which nginx and Traefik do by default.

**Speech-to-text (C6)** and **rerank (C5)** as described in section 6. The upload is still read into memory; `ROUTER_MAX_BODY_MB` bounds it.

### 7.6 Usage and token accounting

**Where token counts come from.**

- Non-streaming JSON responses: the `usage` object of the response (`prompt_tokens`, `completion_tokens`; embeddings and rerank report prompt or total tokens only).
- Streaming chat: backends only send usage when the request asks for it. If the client did not set `stream_options.include_usage`, the router sets it on the upstream request and removes the resulting usage-only event (empty `choices`, `usage` present) from the stream it relays. If the client did ask, the stream is relayed untouched and usage is read from it. Either way the client receives exactly what it would have received before. A route can opt out with `usage_from_stream: false` if its backend rejects the option.
- Speech endpoints have no token counts; requests are counted.

Token extraction is best effort. A response the router cannot parse is still delivered, with no token count.

This needs an SSE splitter that turns arbitrary byte chunks into whole events and relays them byte-exact. Only whole events are ever dropped, and only the usage-only event described above.

**Outcome of a request** (one per request, used in usage, metrics and logs): `ok`, `client_error` (backend 4xx), `backend_error` (backend 5xx), `unreachable`, `timeout`, `midstream_error`, `client_closed`, and the non-forwarded ones `unavailable`, `rate_limited`, `concurrency_limited`. Streaming requests are recorded when the stream ends, not when it starts, so a stream that breaks is no longer counted as a success.

**Storage.**

- `usage_daily` keeps its exact meaning, so the Streamlit UI shows the same numbers: one increment per forwarded request, `model` is the route name, `error_count` covers every forwarded outcome except `ok` and `client_closed`.
- New table `usage_hourly`, keyed by (hour in UTC, key id, model id as the client sent it, route name, outcome), with `owner`, `requests`, `prompt_tokens`, `completion_tokens` and `duration_ms_sum`. It records all outcomes, including the non-forwarded ones. No foreign key to `api_keys`, same as today, so history survives key deletion.
- The writer stays off the request path (queue plus batched upserts). On a database error it retries the batch a few times with backoff before dropping it, counts drops in a metric, and flushes on shutdown.
- Requests rejected before a key and model are known (401, 404, 400, 403) appear in metrics and logs only.

### 7.7 Logs and metrics

**One log line per request** when it ends, in text or JSON: request id, key id and name, owner, model id, route, backend, endpoint, status, outcome, upstream status, stream flag, attempts, duration, time to first byte, prompt and completion tokens. Prompts and responses are never logged at INFO.

**Prometheus** on `ROUTER_METRICS_PORT`, without auth, meant for internal scraping only. Suggested set (names are the builder's call):

- requests by route, model, key and outcome; tokens by route, model, key and kind;
- request duration and time-to-first-byte histograms per route, with buckets up to 600 s;
- in-flight requests per route; rejections by reason (auth, rate, concurrency, unavailable);
- backend up/down per route and backend; probe duration; backend failures by kind;
- keys loaded, key-cache age, usage queue depth, usage records dropped, config reload results;
- build info with version and git commit.

The same port serves the detailed health view (today's `/health` content: per-backend state with URLs, key-store state).

The image carries its git commit (build argument), exposed in `/health` and the build-info metric, so "which version is actually running" has a direct answer.

### 7.8 Public surface

`/livez` is unchanged. Public `/health` always answers 200 and reports cached state (C8). `/v1/models` and `/v1/audio/voices` stay unauthenticated; voices is cached (C15). The docs endpoints are off by default (C10). Unknown paths use the error envelope (C12).

### 7.9 Upstream-router mode

Purpose: a second site reaches another site's models through that site's router, as one upstream with one key, without copying its route list.

```yaml
upstreams:
  - name: "site-a"
    base_url: "http://site-a-router:8091"
    api_key_env: "SITE_A_ROUTER_API_KEY"
    health_path: "/livez"          # default
    include: ["*"]                 # optional id globs
    exclude: []
    discovery_interval: 60         # seconds, default
    proxy: null                    # optional, same meaning as on a route
    tls_verify: true               # optional, for https upstreams
    ca_file: null                  # optional CA bundle for https upstreams
```

The first real use needs none of the last three: the link is plain HTTP inside an existing tunnel (details in the private notes). They are in the schema so that a different path later does not need a new release.

- Every id the upstream lists in `/v1/models` (after include/exclude) becomes routable. Requests are forwarded to the same path on the upstream with the upstream key. The `model` field is not rewritten and no `request_defaults` are applied: aliases and quirks are the upstream's business.
- Local `routes` win over upstream ids. Between upstreams, the first in config order wins.
- Route name and type of an upstream id come from the upstream's `x_router` object (C14). The route name is used as is for allowlists and usage, so numbers stay comparable across sites. An upstream without `x_router` (an older router) yields route name = upstream name and no type check; the upstream enforces types itself.
- The upstream is probed like a backend. When it is down, or stops listing an id, the id stays known and returns 503.
- `/v1/audio/voices` for an upstream text-to-speech id is forwarded to the upstream with `?model=<id>`.
- Upstream-derived route names are synced into the `routes` table like local ones, and are not deleted while the upstream is unreachable.
- Both routers count usage. Token extraction works on both, because the downstream router's `include_usage` request makes the upstream relay the usage event.
- Tests use two real router instances.

### 7.10 Database and migrations

- Numbered SQL files in `migrations/`, applied in order at startup under an advisory lock, tracked in a `schema_migrations` table. No external migration tool.
- `0001` is the current schema written with `IF NOT EXISTS`, so both live databases adopt it without change. `0002` adds nullable key columns (`max_concurrency`, plus `owner_email`, `created_by` and `notes` for the admin website), `0003` adds `usage_hourly`, `0004` adds the admin website's audit table, `0005` (added during the build) its table of signed-out sessions. The files in `model-orchestrator/migrations/` are the exact schema; the router is the only component that applies them.
- Everything is tested against Postgres 16 and 17.
- A database that is down at startup still fails the start (the supervisor restarts the router). A database that goes away at runtime means degraded mode, as today.

## 8. Testing

Three layers, plus a script for deployed instances.

1. **Unit tests** (no network, no database): config validation including sanitised copies of both live configs, defaults merging, limiter, SSE splitter (including every possible chunk boundary), usage extraction, error mapping, model table and availability logic.
2. **End-to-end contract suite.** The router runs as a black box over HTTP (a subprocess, or a container) against fake backends and a real Postgres.
   - **Fake backends** are small local servers that imitate vLLM chat (non-streaming and SSE, with `reasoning` fields and the usage event), vLLM `/v1/models` and `/health`, TEI embeddings, a reranker, a Whisper-style upload endpoint and a Kokoro-style speech endpoint with voices. They record what they received and can be told to fail: delay, error status, drop the connection mid-stream, never answer, fail health.
   - **Written first against the current router**, before any restructuring. Every test either passes on the old code, or is tagged with a change id from section 6 and states the old behaviour. This suite is the safety net for the restructure and the definition of the contract in section 5.
   - It covers every row of section 5 and section 6, plus: key refresh, database outage, config reload (valid and invalid), availability transitions, failover, cancellation (the fake backend sees the abort), usage rows and token counts, and upstream mode.
   - Postgres comes from a connection string in the environment. A helper starts a throwaway Postgres with Podman locally; CI uses service containers.
3. **Image smoke test in CI:** build the image, start it with a fake backend and Postgres, call `/livez` and one chat request. This catches packaging mistakes.
4. **Live smoke script** for a deployed router, given a URL and a key: lists models, makes one cheap call per endpoint (chat streaming and non-streaming with a reasoning check, embeddings, rerank, a speech round trip, voices), prints a table and sets the exit code. Run by hand after each deploy.

## 9. CI/CD and releases

- **Triggers:** pull requests and pushes that touch `model-orchestrator/**`, excluding Markdown files.
- **Jobs:** lint and format check, type check, unit tests, end-to-end suite (Postgres 16 and 17), image build, image smoke test. Publishing needs all of them.
- **Tags:** every push to `main` publishes `sha-<short>`. **`latest` moves only on a release tag** (`model-orchestrator-vX.Y.Z`, which also publishes `vX.Y.Z`). The first release after the restructure is `3.0.0`.
  - Reason: one site auto-updates from `latest` every night. With `latest` tied to releases, work on `main` can be piloted at the other site by `sha-` pin while the auto-updating site stays on its current image until a release is cut on purpose. **This CI change is the first commit of the project**, before anything else under `model-orchestrator/` changes on `main`.
- **Dependencies:** `uv.lock` committed, image built with the frozen lock. Dependabot (or Renovate) opens weekly update pull requests for Python packages, the base image and the workflow actions, and those run the full suite. *As built:* `.github/dependabot.yml` covers Python packages and base images of the router and the admin website. Workflow actions are left out, because the same updater would also change another workflow in this repository whose `latest` tag still moves on every push to `main` and is auto-updated by a production host.
- **Admin UI image:** the same rule, because one site auto-updates it from `latest` too. A push to `main` that touches `admin_ui/**` publishes `sha-<short>` only; its `latest` moves only on its own release tag (`model-orchestrator-admin-ui-vX.Y.Z`). This project does not touch `admin_ui/`, so that tag is not expected to be used. No automated dependency updates for `admin_ui/`; it is being replaced.
- **Deployment stays as each site has it:** GitOps with a pinned tag at the homelab, registry auto-update at the university.

## 10. Rollout

| Phase | What | Where |
|---|---|---|
| 0 | CI change (`latest` on release tags only). Contract suite against the current code, running in CI. No behaviour change. | repo |
| 1 | Restructure into the package with no behaviour change; the suite stays green with the same expectations. | homelab, by `sha-` pin |
| 2 | Fixes and features in small steps, each deployed and smoke-tested: error handling and streaming status; speech-to-text and rerank fields; cancellation; migrations, hourly usage and tokens; logs and metrics; health and availability; config validation and reload; concurrency cap. | homelab |
| 3 | Upstream-router mode. | repo, tested with two instances |
| 4 | After the freeze: release `3.0.0`, university site upgrades, live smoke test, config additions (health paths, concurrency caps). | university |
| 5 | Homelab switches its university routes to one upstream entry. | homelab |

After phase 5 the admin website project starts.

Before the release in phase 4, two checks at the auto-updating site: its live config passes `check-config`, and every probed backend reports healthy on the running router. The config additions in phase 4 are improvements, not prerequisites; the live config works unchanged.

`roadmap.md` in this directory already contains an internal address, an email address and account names. They stay in the git history, but the rewritten `README.md` and `roadmap.md` must not carry them forward.

Repository docs to update along the way: `README.md`, `roadmap.md`, `config.example.yaml` (currently stale), the Quadlet units and Kubernetes templates under `deployment/` (health command on `/livez`, metrics port, no sidecar without a readiness probe), and a `docker-compose.yaml` that starts router, Postgres and a fake backend so the stack runs locally without a GPU.

## 11. Left to the builder

Module and file names; lint and type-check tools; metric names and histogram buckets; the SSE splitter's implementation; how the fake backends are controlled; the migration runner's details; exact wording of error messages; text log layout; how the per-minute limiter cleans up idle keys.

## 12. Follow-ups (separate work)

- Admin website with Keycloak login, with an admin API as its backend. *Built on the same branch, without the admin API: see [2026-11-19-admin-website-design.md](2026-11-19-admin-website-design.md).*
- Token budgets per key; load-aware balancing; more than one replica.
- Database backups at both sites.
- Health-gated start for the auto-updating site, so a bad image rolls back by itself.
- Restricting model servers to accept traffic from the router only.
- A scheduled synthetic check built on the live smoke script.

## 13. As built (2026-11-19)

The package `model_orchestrator/` implements sections 5 to 7. Verified on 2026-11-19: 492 unit tests; the end-to-end suite with 482 passed and 43 skipped against the new router, and 304 passed, 109 expected failures and 112 skipped against the old one (the expected failures are the tests for section 6, each failing on the old code for the reason it states); lint and strict type check clean.

Where the code decides something this document left open, or goes beyond it:

| Area | As built |
|---|---|
| Discovered ids | An id that a reachable backend no longer lists returns 503 and is not listed. This applies to local routes as well as upstreams. |
| Failover | With two failed attempts, the most recent thing a backend actually did decides between its own 5xx and the 502. An attempt that reached no backend never replaces either. A retry does not advance the round-robin position. |
| Upstream errors | An upstream router's `code` and `Retry-After` are passed on, so a client gets the same answer through two routers as through one. |
| Broken streams | A half-received event is dropped before the error event is sent. An error event sent by the backend itself counts as `midstream_error`. A broken streamed body that is not SSE aborts the connection. |
| Health | Public `/health` is `degraded` only when the database is unavailable, as before. Database state comes from the last database operation, not a live ping. |
| Internal port | If the metrics port cannot be bound, the router logs an error and keeps serving the API. `ROUTER_METRICS_PORT=0` switches the listener off. |
| Discovery | A target that cannot be reached is asked again after 15, 30, 60 and 120 s. This covers routes without a health probe. |
| Validation | Also rejected: duplicate YAML keys, a backend URL without `http://` or `https://`, an `endpoint` or `health_path` without a leading slash, an unknown proxy scheme, and the same URL twice in one pool. |
| Order of checks | The body-size check (413) runs after auth. For speech-to-text, the missing-file 400 comes before the model lookup. |
| `model` values | A `model` that is a number, boolean, list or object is a 400. Missing, null or empty stays a 404. |
| Success responses | The backend's status code and content type are relayed as they are, for streams too. |
| `routes` table | Nothing is deleted from it while any upstream is configured, because an upstream route that is temporarily down is not listed. |
| Overload | Health probes and discovery have their own connections, so traffic cannot starve them. Forwarded requests share a pool of `ROUTER_UPSTREAM_MAX_CONNECTIONS` (1000); a request that finds none free within 5 s gets 503 `router_busy`. A stream whose client stops reading for `ROUTER_CLIENT_WRITE_TIMEOUT` (60 s) is dropped. `ROUTER_DEFAULT_MAX_CONCURRENCY` (off by default) caps keys that have no cap of their own. |
| Shutdown | On SIGTERM requests in flight get `ROUTER_SHUTDOWN_GRACE_SECONDS` (8). What still runs then is ended with code `router_shutting_down` and recorded; usage is flushed and the process exits by itself. |
| Voices | One backend call per cache key however many callers ask at once; a failure is remembered for 5 s. |
| Streams | A connection that breaks after `[DONE]` was relayed counts as a clean end. |
| Reload | `SIGHUP` reloads at once; the two-stable-polls rule applies to file watching only. Backend key variables are re-read on reload. `ROUTER_MIGRATIONS_DIR` overrides where migrations are read from. |

The overload, shutdown, voices and stream rows come from an independent code review after the first implementation; each has tests that fail on the pre-fix code.

Also built on the same branch: the admin website ([its design record](2026-11-19-admin-website-design.md), 209 tests, independently security-reviewed), `docker-compose.yaml` with fake backends as a demo stack without a GPU, the live smoke script, rewritten `README.md`, `roadmap.md`, `config.example.yaml` and deployment files, and the CI workflow of section 9.

Known gaps: HTTP clients created for a proxy that a reload removed stay open until shutdown. The 200 MB body check runs at 32 MB by default in the suite. The live smoke script (`scripts/smoke.py`) has only run against the local demo stack, not a real deployment. With a read-only root filesystem, an upload over 1 MB needs a writable `/tmp`. The CI workflow has not run on GitHub yet.
