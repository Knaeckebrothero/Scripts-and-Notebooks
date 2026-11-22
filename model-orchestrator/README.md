# Model Orchestrator

One door to every self-hosted model. The router is an OpenAI-compatible HTTP service that sits in front
of model servers (vLLM, Text Embeddings Inference, Whisper, Kokoro and anything else that speaks the same
API) and gives them one address, one set of API keys, per-key limits, usage and token accounting, health
checks and metrics.

Version 3 is a rebuild of the 2.x router as a tested Python package. If you run 2.x, read
[Upgrading from 2.x](#upgrading-from-2x) first.

**Contents:** [What it is](#what-it-is-and-what-it-is-not) · [Quick start](#quick-start) ·
[Architecture](#architecture) · [API](#api) · [Configuration](#configuration) ·
[Keys and limits](#keys-rate-limit-and-concurrency-cap) · [Usage](#usage-accounting) ·
[Logs and metrics](#logs-and-metrics) · [Health](#health-checks-and-availability) ·
[Database](#database-and-migrations) · [Deployment](#deployment) · [Admin website](#admin-website) ·
[Smoke test](#smoke-test-for-a-deployed-router) · [Development](#development-and-testing) ·
[Releases](#releases-and-image-tags) · [Upgrading](#upgrading-from-2x)

## What it is, and what it is not

The router does five things to a request: it checks the API key, finds the backend for the `model`,
applies the key's limits, forwards the request, and records what happened.

| It is | It is not |
|---|---|
| A pass-through. Bodies go to the backend as the client sent them, and answers come back as the backend sent them. Unknown fields pass in both directions. | A translation layer. It does not convert between API dialects, rename fields or normalise answers. A backend must already speak the OpenAI-style API for its endpoint. |
| A fixed set of endpoints: chat, embeddings, rerank, speech-to-text, text-to-speech, model list. | A general reverse proxy. `/v1/completions`, `/v1/responses` and image endpoints are not served. |
| Round robin over a pool of identical backends, with one retry on another backend. | A load-aware balancer or a GPU scheduler. It does not start, stop or swap models. |
| One process and one replica. Limits and health state live in memory. | Horizontally scalable. A second replica would give every key twice its limits. |
| Token accounting per key and model. | Token budgets or quotas. Tokens are counted, not limited. |

The pass-through is the property to protect. It is why a new backend feature, such as the `reasoning`
field of vLLM, reaches clients without a router change.

## Quick start

The demo stack runs the router against fake model servers. It needs Podman or Docker and no GPU. The
answers are made up; everything else (keys, limits, usage, health, metrics, the admin website) is real.

```bash
cd model-orchestrator
podman compose up --build -d      # or podman-compose, or docker compose
```

The first build takes a minute or two. Then:

1. Open <http://127.0.0.1:8600> and sign in as `admin` with the password `demo-password-123`.
2. Go to **Keys**, choose **Create key**, give it a name and an owner, and copy the secret. It is shown
   once. The router accepts a new key within 15 seconds.
3. Call the API:

```bash
export KEY=sk-mo-...        # the secret you copied
R=http://127.0.0.1:8090

# Chat
curl -s $R/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model": "demo-chat", "messages": [{"role": "user", "content": "Hello router"}]}'

# Chat, streaming
curl -sN $R/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model": "demo-chat", "stream": true, "messages": [{"role": "user", "content": "Hello router"}]}'

# Embeddings
curl -s $R/v1/embeddings -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model": "demo-embed", "input": ["first text", "second text"]}'

# Text-to-speech, then speech-to-text on the result
curl -s $R/v1/audio/speech -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model": "tts-1", "input": "Hello from the demo", "voice": "af_heart"}' -o speech.mp3
curl -s $R/v1/audio/transcriptions -H "Authorization: Bearer $KEY" -F model=whisper-1 -F file=@speech.mp3
```

Things to look at next:

| What | Where |
|---|---|
| The models the router offers | `curl -s $R/v1/models` |
| Status per route | `curl -s $R/health` |
| Usage per key, with tokens | the **Usage** page of the admin website |
| One log line per request | `podman compose logs -f router` |
| Prometheus metrics and the detailed health view | `curl -s http://127.0.0.1:9095/metrics` and `.../health` |
| Every endpoint type in one go | `SMOKE_API_KEY=$KEY uv run python scripts/smoke.py --base-url $R` |
| A config reload | edit `demo/config.yaml` (add an alias under `models:`); it is live within about 20 seconds |
| A backend outage | `podman compose stop backends`: after about half a minute the probed models leave `/v1/models` and answer 503. `podman compose start backends` brings them back |

`podman compose down -v` stops the stack and deletes the demo database.

If a port is taken, choose others: `DEMO_ROUTER_PORT=18090 DEMO_ADMIN_PORT=18600 DEMO_METRICS_PORT=19095
podman compose up --build -d`. The stack is described in `docker-compose.yaml`; its route config is
`demo/config.yaml` and its fake backends are `scripts/demo_backends.py`.

## Architecture

```
                 clients
                    |   HTTPS (a TLS proxy in front: nginx, Traefik, ...)
                    v
  +----------------------------------------------+        +---------------------+
  | router                                       |  HTTP  | model servers       |
  |   :8090  API                                 | -----> | vLLM, TEI, Whisper, |
  |     auth -> model lookup -> limits ->        |        | Kokoro, ...         |
  |     availability -> forward                  |        +---------------------+
  |   :9095  internal: /metrics, detailed /health| -----> another router
  +-----------------------+----------------------+        (upstream mode)
                          |
                          |  keys (cached), usage, route names
                          v
                      Postgres  <-----  admin website :8600
                                        (keys, usage, audit log)
```

A request passes these checks in this order. The first one that fails answers.

| Step | Fails with |
|---|---|
| 1. API key | 401 |
| 2. Body is read and parsed | 413, 400 |
| 3. `model` is a known id | 404 |
| 4. The model's type fits the endpoint | 400 |
| 5. The key may use the route | 403 |
| 6. Requests per minute of the key | 429 |
| 7. Requests in flight of the key | 429 `concurrency_limit_exceeded` |
| 8. The model has a backend that is up | 503 `model_unavailable` |
| 9. Forward to a backend | the backend's answer, or 502, 503, 504 |

Requests rejected in steps 1 to 5 do not count against the per-minute limit. A request that passes
step 6 counts, whatever happens to it afterwards.

The code is the package `model_orchestrator/`. Nothing happens at import time: an app factory takes the
settings and the config, and all runtime state hangs off one object created when the app starts.

| Module | Responsibility |
|---|---|
| `settings`, `config` | Environment variables; the YAML schema, validation |
| `registry`, `discovery` | The model table: aliases and discovered ids to routes; asking backends for `/v1/models` |
| `health` | Active probes, passive marks, backend selection |
| `auth`, `limits` | Bearer key to key record; per-minute window and concurrency cap |
| `api`, `payload` | Endpoint table and handlers; the few changes made to a request body |
| `proxy`, `sse`, `clients` | Forwarding, failover, cancellation; event-stream splitting and usage extraction; outbound HTTP clients |
| `usage`, `db` | Usage events and the background writer; pool, migrations, key loading |
| `metrics`, `logs`, `errors` | Prometheus, request log, error envelopes |
| `internal`, `state` | The internal listener; the object that wires everything together and runs reloads |

## API

Every path is the OpenAI path. Point an OpenAI client at `https://<router>/v1` with a router key.

### Endpoints

| Method and path | Route types | Auth | Notes |
|---|---|---|---|
| `POST /v1/chat/completions` | `chat`, `vision` | key | Streaming with `"stream": true` |
| `POST /v1/embeddings` | `embedding` | key | |
| `POST /v1/rerank` | `reranker` | key | |
| `POST /v1/audio/transcriptions` | `stt` | key | Multipart upload. Every form field is forwarded |
| `POST /v1/audio/speech` | `tts` | key | JSON in, audio bytes out |
| `GET /v1/audio/voices[?model=]` | `tts` | none | The backend's voice list, cached for 5 minutes per route. However many callers ask at once, the backend is asked once, and a failure is remembered for a few seconds |
| `GET /v1/models` | – | none | The model ids that can be used right now |
| `GET /v1/models/{id}` | – | none | One id. Ids containing `/` work |
| `GET /livez` | – | none | 200 while the process runs. For container and kubelet probes |
| `GET /health` | – | none | Overall status, version, database status, up or down per route. Always 200 |

`/docs`, `/redoc` and `/openapi.json` are off unless `ROUTER_EXPOSE_DOCS=1`. Metrics and the detailed
health view are on a separate internal port, see [Logs and metrics](#logs-and-metrics).

### Model ids and aliases

A route answers to two kinds of model id:

- **Aliases**, the `models:` list of the route. The request's `model` is rewritten to the route's
  `backend_model_name` before it is forwarded.
- **Discovered ids**, everything the backend lists in its own `/v1/models`. They are forwarded as sent.

An alias wins over a discovered id. Among discovered ids the first route in config order wins. Local
routes win over ids from an upstream router.

`/v1/models` lists the ids that are available now. Where the backend has a model list of its own, an
entry is the backend's entry (with `max_model_len` and whatever else it reports); otherwise it is a
minimal one. The router adds an `x_router` object. Shortened:

```json
{"id": "demo-chat", "object": "model", "owned_by": "demo", "max_model_len": 32768,
 "x_router": {"route": "Demo-Chat", "type": "chat"}}
```

The route name in `x_router` is what key allowlists and the usage tables use.

### What the router changes in a request

Nothing but this:

| Change | When |
|---|---|
| `model` is rewritten to `backend_model_name` | The client used an alias |
| The route's `request_defaults` are merged in | JSON endpoints. A value the client sent always wins. A mapping (for example `chat_template_kwargs`) is merged key by key, one level deep |
| `stream_options.include_usage` is set to true | Streaming chat when the client did not ask for it. The extra usage event is removed from the answer, so the client sees what it would have seen anyway. Off per route with `usage_from_stream: false` |
| `top_n: null` is dropped | Rerank. A number is forwarded |
| A missing `model` form field is filled in | Speech-to-text: the first `stt` route is used |
| `Authorization` is replaced | Always. The client's key never reaches a backend. The route's own key is sent when `backend_api_key_env` is set |

JSON request bodies are parsed and written out again, so whitespace may differ; fields and values do not.
Answers are relayed as the backend sent them. The `reasoning` and `delta.reasoning` fields reach the
client untouched.

### Authentication

`Authorization: Bearer <key>`. A missing, unknown, disabled or expired key gets the same 401. With no
keys in the database every request is rejected; there is no anonymous mode. `GET /v1/models`,
`/v1/audio/voices`, `/livez` and `/health` need no key.

### Headers

| Header | On | Meaning |
|---|---|---|
| `X-Request-ID` | every response | Echoed if the client sent one, otherwise generated. Also sent to the backend and written in every log line |
| `X-RateLimit-Limit`, `-Remaining`, `-Reset` | every response once the per-minute limit has been checked, errors and streams included | The key's requests per minute, what is left in the window, and the epoch second at which the oldest counted request leaves it |
| `X-Router-Error-Source` | error responses | `router` or `backend`: who produced the error |
| `Retry-After` | 503 `model_unavailable` (30), 503 `router_busy` (1), 429 `concurrency_limit_exceeded` (1) | Seconds |

### Errors

Errors use the OpenAI envelope. `code` is set only where the table names one.

```json
{"error": {"message": "Model 'demo-chat' is temporarily unavailable", "type": "api_error", "code": "model_unavailable"}}
```

| Status | `code` | When | Source |
|---|---|---|---|
| 400 | – | Body is not JSON or not a JSON object; `model` is not a string; the model is of another type than the endpoint; speech-to-text without `file` or without a multipart body | router |
| 401 | – | Key missing, unknown, disabled or expired | router |
| 403 | – | The key's allowlist does not contain the route | router |
| 404 | – | Unknown model id; unknown path | router |
| 405 | – | Wrong method | router |
| 413 | – | Body larger than `ROUTER_MAX_BODY_MB` | router |
| 429 | – | The key's requests per minute are used up | router |
| 429 | `concurrency_limit_exceeded` | The key has too many requests in flight | router |
| 500 | – | An unexpected error inside the router | router |
| 502 | `backend_disconnected` | The backend closed the connection before a complete answer | router |
| 503 | `model_unavailable` | The model is known, but every backend of its route is down. Not forwarded | router |
| 503 | `backend_unreachable` | No backend could be reached | router |
| 503 | `router_busy` | The router has no free connection to the backends (`ROUTER_UPSTREAM_MAX_CONNECTIONS`) and none came free within 5 seconds | router |
| 503 | `router_shutting_down` | The router is shutting down and the request could not finish in the grace period | router |
| 504 | `backend_timeout` | The backend did not answer within the read timeout | router |
| the backend's status | – | The backend answered with an error. The status is the backend's and `message` is the backend's own message | backend |

`type` follows the status: `authentication_error` (401), `permission_error` (403), `rate_limit_error`
(429), `api_error` (5xx), `invalid_request_error` (everything else). Status, `type` and `code` are the
contract. The wording of `message` is not. Router messages never contain backend URLs.

### Streaming

- Server-sent events are relayed byte for byte, one whole event at a time.
- The router waits for the backend's response headers before it answers. A failure before the first
  byte is therefore a real HTTP status with a JSON error, exactly as for a request without streaming.
- A failure after the stream has started is reported in the stream: one event
  `data: {"error": {"message": "...", "type": "api_error"}}`, then `data: [DONE]`. When the cause is a
  shutdown of the router, the event carries `"code": "router_shutting_down"`.
- A backend connection that breaks after `[DONE]` has been relayed is not an error: the client has the
  whole stream.
- When the client goes away, the backend call is cancelled. That is true without streaming as well, so an
  abandoned request stops using the GPU. A proxy in front has to pass the disconnect on; nginx and
  Traefik do.
- A client that stays connected but reads nothing for `ROUTER_CLIENT_WRITE_TIMEOUT` (60 seconds) is
  dropped. Its backend call is cancelled and its concurrency slot is free again.

### Timeouts and retries

The connect timeout is 10 seconds on every endpoint. The read timeout depends on the endpoint:

| Endpoint | Read timeout |
|---|---|
| Chat (and vision) | `CHAT_READ_TIMEOUT_S`, default 600 s |
| Speech-to-text | 300 s |
| Embeddings, rerank, text-to-speech | 60 s |
| Voices | 30 s |

A route can set its own read timeout with `timeout_s`.

In a pool, a request gets at most two attempts, on different backends. A second attempt follows a failed
connect, a broken connection or a 5xx answer, for streaming requests too as long as nothing has been sent
to the client. It never follows a read timeout or a 4xx answer.

The router keeps at most `ROUTER_UPSTREAM_MAX_CONNECTIONS` (default 1000) connections to backends per
network path, that is per proxy and TLS setting. A request that gets none within 5 seconds is answered
503 `router_busy` with `Retry-After: 1`. No backend is blamed for it, and there is no second attempt.
Health probes and model discovery use connections of their own, so heavy traffic cannot make a healthy
backend look down.

Request bodies up to `ROUTER_MAX_BODY_MB` (default 200) are accepted and held in memory while they are
forwarded.

**Shutdown.** On `SIGTERM` or `SIGINT` the router accepts no new connections and gives the requests in
flight `ROUTER_SHUTDOWN_GRACE_SECONDS` (default 8) to finish. What is still running then is ended with
an answer instead of a cut connection: 503 `router_shutting_down`, or for a stream the error event
above. Usage is written and the process exits by itself. Whatever stops the container has to wait longer
than the grace period; see [Deployment](#deployment) for raising both.

## Configuration

Two places: environment variables for the process, one YAML file for the routes. API keys are in neither.
They live in Postgres.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_DATABASE_URL` | required | Postgres connection string. The router does not start without it |
| `ROUTER_CONFIG` | `config.yaml` | Path of the route config. `/app/config.yaml` in the image |
| `ROUTER_HOST` | `server.host` of the config, else `0.0.0.0` | Listen address of the API |
| `ROUTER_PORT` | `8090` | Port of the API |
| `ROUTER_METRICS_PORT` | `9095` | Internal port for `/metrics` and the detailed `/health`. `0` switches it off |
| `ROUTER_METRICS_HOST` | same as the API address | Listen address of the internal port |
| `ROUTER_LOG_LEVEL` | `INFO` | |
| `ROUTER_LOG_FORMAT` | `text` | `text` or `json` |
| `CHAT_READ_TIMEOUT_S` | `600` | Read timeout for chat and vision |
| `ROUTER_MAX_BODY_MB` | `200` | Larger requests get 413 |
| `ROUTER_UPSTREAM_MAX_CONNECTIONS` | `1000` | Connections to backends per network path (proxy and TLS setting). Beyond it requests wait 5 seconds, then get 503 `router_busy` |
| `ROUTER_CLIENT_WRITE_TIMEOUT` | `60` | Seconds a streamed answer may be stuck because the client is connected but not reading. Then the stream is dropped and the backend call cancelled. `0`: off |
| `ROUTER_DEFAULT_MAX_CONCURRENCY` | `0` | Cap on requests in flight for keys that have none of their own. A key's own value always wins. `0`: no default cap |
| `ROUTER_SHUTDOWN_GRACE_SECONDS` | `8` | After `SIGTERM`: how long requests in flight may run before the router ends them. Must be shorter than the stop timeout of whatever runs the container |
| `ROUTER_KEYS_REFRESH_SECONDS` | `15` | How often keys are re-read from Postgres. A new, changed or disabled key takes effect within this time |
| `ROUTER_HEALTH_INTERVAL` | `10` | Seconds between health probes |
| `ROUTER_HEALTH_TIMEOUT` | `5` | Seconds a probe may take |
| `ROUTER_HEALTH_FAILS` | `3` | Failed probes in a row before a backend counts as down |
| `ROUTER_HEALTH_GATING` | `on` | `off`: probes still run and are reported, but no longer decide whether a model is available |
| `ROUTER_UNHEALTHY_SECONDS` | `30` | How long a backend is avoided after a failed request. `0` switches that off |
| `ROUTER_DISCOVERY_INTERVAL` | `300` | Seconds between `/v1/models` calls to each backend |
| `ROUTER_CONFIG_POLL_SECONDS` | `10` | How often the config file is checked for changes. `0` switches the check off; `SIGHUP` still reloads |
| `ROUTER_EXPOSE_DOCS` | `off` | Serve `/docs`, `/redoc` and `/openapi.json` |
| `ROUTER_MIGRATIONS_DIR` | `migrations/` next to the package | Where the SQL migrations are |
| `ROUTER_VERSION`, `ROUTER_GIT_SHA` | set by the image build | Reported in `/health`, logs and `router_build_info` |

Switches accept `1/0`, `true/false`, `yes/no`, `on/off`. A value that cannot be used stops the start with
a message that names the variable (exit code 2).

### Route config

`config.example.yaml` is a complete, commented example. The schema:

**`server`** (optional)

| Key | Meaning |
|---|---|
| `host` | Listen address, used when `ROUTER_HOST` is not set. Read at start only |
| `port` | Accepted and ignored. The port is `ROUTER_PORT`. A warning is logged if the two differ |

**`routes[]`**

| Key | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | | The route name. Key allowlists, usage and logs use it |
| `type` | yes | | `chat`, `vision`, `embedding`, `reranker`, `stt` or `tts` |
| `backend` | yes | | Base URL of the model server, or a list of URLs (a pool of servers with the same model) |
| `endpoint` | yes | | Path on the backend, for example `/v1/chat/completions` |
| `backend_model_name` | | the alias | The backend's own name for the model. Aliases are rewritten to it |
| `models` | | none | Aliases clients may use |
| `owned_by` | | what the backend reports | `owned_by` in `/v1/models` |
| `request_defaults` | | none | Fields merged into every request of this route. The client's value wins |
| `health_path` | | `/health` | Path that is probed. `null` or `""`: no probe, the route always counts as available |
| `proxy` | | none | `http://`, `https://`, `socks5://` or `socks5h://` proxy for requests and probes of this route |
| `backend_api_key_env` | | none | Name of an environment variable whose value is sent to the backend as bearer token |
| `timeout_s` | | per endpoint | Read timeout for this route, in seconds |
| `usage_from_stream` | | `true` | `false`: do not ask the backend for the usage event of streamed answers |

**`upstreams[]`** (optional), see [Another router as an upstream](#another-router-as-an-upstream)

| Key | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | | Name of the upstream |
| `base_url` | yes | | The other router, `http://` or `https://` |
| `api_key_env` | | none | Environment variable holding a key of that router |
| `health_path` | | `/livez` | Probed like a backend. `null`: no probe |
| `include` / `exclude` | | `["*"]` / `[]` | Globs: which of its model ids to take over |
| `discovery_interval` | | `60` | Seconds between `/v1/models` calls |
| `proxy` | | none | As on a route |
| `tls_verify`, `ca_file` | | `true`, none | For an `https://` upstream |

### Validation

The config is validated as a whole, and every problem is reported at once with the route name and the
key. These are errors:

- an unknown key anywhere (a misspelt `request_defaults` would otherwise be ignored silently), or the
  same key twice in one mapping;
- a missing `name`, `type`, `backend` or `endpoint`; an unknown `type`;
- a value of the wrong type. Nothing is converted: `"8090"` is not a number and `"no"` is not a boolean;
- a `backend` or `base_url` that is not an `http://` or `https://` URL; the same URL twice in one pool;
  an `endpoint` or `health_path` that does not start with `/`; an unknown `proxy` scheme; a `timeout_s`
  or `discovery_interval` that is not above zero;
- two routes with the same name, two routes sharing an alias, two upstreams with the same name, an
  upstream named like a route.

These are warnings: an environment variable named by `backend_api_key_env` or `api_key_env` that is not
set (requests then carry no `Authorization`), and a config without any route or upstream.

Check a file before you deploy it. The command needs no database and starts nothing:

```console
$ python -m model_orchestrator check-config config.yaml
config.yaml: OK (8 route(s), 13 alias(es), 0 upstream(s))

$ python -m model_orchestrator check-config broken.yaml
broken.yaml: INVALID
  - route 'Demo-Chat' (routes[0]): unknown key 'request_default' (did you mean 'request_defaults'?)
```

It exits non-zero on an invalid file, so a config repository can run it in CI. With the image:
`podman run --rm -v ./config.yaml:/app/config.yaml:ro,z <image> python -m model_orchestrator check-config /app/config.yaml`.

At start an invalid config stops the router (exit code 1).

### Reload

The router re-reads the config file on `SIGHUP` and when the file changes (checked every
`ROUTER_CONFIG_POLL_SECONDS`).

- A valid config replaces the route table in one step. Requests in flight finish on the old one. Health
  state and discovered ids of routes that stay are kept; new backends are probed at once.
- An invalid config is rejected. The running config stays, the error is logged, the internal `/health`
  shows it as `config.last_reload_error`, and `router_config_reloads_total{result="error"}` goes up.
- A reload never empties the route table: a file without routes and upstreams is refused.
- A changed file is acted on once it has looked the same on two checks in a row, so a half-written file
  is not loaded. With the default interval a change is live after 10 to 20 seconds.
- The `server` block and all environment variables apply at start only. A new value for a backend key
  needs a restart.

The router must be able to see the new file. A file bind-mounted on its own only shows edits made in
place, and most editors replace the file instead. Mount the directory that holds the config, or restart.
On Kubernetes a ConfigMap mounted with `subPath` never updates; mount it as a directory.

### Another router as an upstream

A site can use another site's models through that site's router, as one upstream with one key, without
copying its route list.

```yaml
upstreams:
  - name: "site-a"
    base_url: "https://router.site-a.example"
    api_key_env: "SITE_A_ROUTER_API_KEY"
```

- Every model id the upstream lists (after `include` and `exclude`) becomes usable. Requests go to the
  same path on the upstream, with the upstream key. `model` is not rewritten and no `request_defaults`
  apply: aliases and quirks are the upstream's business.
- Local routes win over upstream ids. Between upstreams the first in config order wins.
- Route name and type of an upstream id come from the upstream's `x_router` object, so allowlists and
  usage numbers stay comparable across sites. A 2.x upstream has no `x_router`: its ids are filed under
  the upstream's name and the type check is left to the upstream.
- The upstream is probed like a backend. When it is down, or stops listing an id, the id stays known and
  answers 503.
- Errors of the upstream keep their `code` and `Retry-After`, so a client gets the same answer through
  two routers as through one.
- Both routers count usage, tokens included.

## Keys, rate limit and concurrency cap

Keys are rows of the table `api_keys`. Manage them in the [admin website](#admin-website). The router
reads the table every `ROUTER_KEYS_REFRESH_SECONDS` and keeps it in memory, so a request never waits for
the database, and a database outage does not stop requests: the last known keys keep working.

| Column | Meaning |
|---|---|
| `key_hash`, `key_prefix` | SHA-256 (hex) of the whole key, and its first characters for display. The key itself is not stored and cannot be recovered |
| `name`, `owner` | Labels. They appear in logs, metrics and usage |
| `rate_limit_rpm` | Requests per minute over a sliding 60-second window. Default 100 |
| `max_concurrency` | Requests in flight at once. `NULL`: no cap |
| `allowed_models` | Route names the key may use. `NULL` or empty: all routes |
| `enabled`, `expires_at` | A disabled or expired key gets 401 |
| `last_used_at` | Updated at most once a minute per key |
| `owner_email`, `created_by`, `notes` | Used by the admin website only |

**Rate limit.** A request over the limit gets 429 and is not counted. The `X-RateLimit-*` headers say
where the key stands.

**Concurrency cap.** A request counts as in flight from the moment it is admitted until its response has
been sent or aborted; for a stream, until the stream ends. Over the cap is 429 with code
`concurrency_limit_exceeded` and `Retry-After: 1`. Use it for keys that send long requests in parallel.
`ROUTER_DEFAULT_MAX_CONCURRENCY` sets a cap for every key that has none of its own; a key's own value
always wins.

Both limits are per key and live in the router's memory. A restart resets them.

A key without the website, for scripts and first installs (the format is free; the website makes
`sk-mo-` plus 40 hex characters):

```sql
INSERT INTO api_keys (key_hash, key_prefix, name, owner, rate_limit_rpm)
VALUES (encode(sha256('sk-mo-REPLACE-ME'::bytea), 'hex'), substr('sk-mo-REPLACE-ME', 1, 12),
        'first key', 'ops', 100);
```

## Usage accounting

Every request that reached a known key and model becomes one usage record when it ends. A streamed
request is recorded when the stream ends, so a stream that breaks is not counted as a success. Records
are written by a background task; the request never waits for the database.

| Table | One row per | Counts |
|---|---|---|
| `usage_daily` | day, key, route | `request_count` and `error_count` of forwarded requests. `model` holds the route name. Unchanged from 2.x |
| `usage_hourly` | hour (UTC), key, model id as the client sent it, route, outcome | `requests`, `prompt_tokens`, `completion_tokens`, `duration_ms_sum`. Also requests that were not forwarded |

Neither table references `api_keys`, so history survives the deletion of a key.

| Outcome | Meaning | Forwarded | Error in `usage_daily` |
|---|---|---|---|
| `ok` | Answered | yes | no |
| `client_error` | The backend answered 4xx | yes | yes |
| `backend_error` | The backend answered 5xx or broke the connection | yes | yes |
| `unreachable` | No backend could be reached, or the router had no free connection (`router_busy`) | yes | yes |
| `timeout` | Read timeout | yes | yes |
| `midstream_error` | The stream broke after it had started, or a shutdown ended it | yes | yes |
| `client_closed` | The client went away first, or stopped reading a stream | yes | no |
| `unavailable` | 503 `model_unavailable`, or 503 `router_shutting_down` before an answer had started | no | – |
| `rate_limited` | 429, per-minute limit | no | – |
| `concurrency_limited` | 429, concurrency cap | no | – |

Requests rejected before key and model are known (401, 404, 400, 403) appear in logs and metrics only.

**Token counts** come from the backend's `usage` object. For streamed chat the router asks the backend
for the usage event itself (see [What the router changes](#what-the-router-changes-in-a-request)).
Embeddings and rerank report prompt tokens only. The speech endpoints have no token counts; their
requests are counted. Counting is best effort: an answer the router cannot read is still delivered,
without a count.

If the database is down, a batch is retried a few times and then dropped. Drops are counted in
`router_usage_records_dropped_total`. Queued records are written at shutdown.

## Logs and metrics

**Logs** go to stdout, as text or as JSON lines (`ROUTER_LOG_FORMAT=json`). Every line carries the
request id. Each request writes one line when it ends:

```
2026-11-19 00:40:06,264 [d2bd67ceb175] INFO model_orchestrator.request: request method=POST
  path=/v1/chat/completions status=200 outcome=ok key_id=1 key=demo-key owner=demo model=demo-chat
  route=Demo-Chat backend=http://backends:9001 endpoint=/v1/chat/completions upstream_status=200
  stream=false attempts=1 duration_ms=7 ttfb_ms=7 prompt_tokens=5 completion_tokens=6
```

(One line in reality.) Prompts and answers are never logged. Calls to `/livez`, `/health` and
`/v1/models` are logged at `DEBUG` only.

**The internal port** (`ROUTER_METRICS_PORT`, default 9095) serves, without auth:

| Path | Content |
|---|---|
| `/metrics` | Prometheus metrics |
| `/health` | The operator's view: every backend with URL, probe state and last error; key cache age; usage queue; when the config was loaded and the last reload error; discovered ids per route |
| `/livez` | 200 |

It shows backend URLs and key names. Scrape it from inside the deployment and never publish it or put it
behind the public proxy. It listens on the same address as the API unless `ROUTER_METRICS_HOST` says
otherwise. If the port cannot be bound, the router logs an error and serves the API anyway.

**Metrics**

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `router_requests_total` | counter | `route`, `model`, `key_id`, `key_name`, `outcome` | Requests that reached a known key and model |
| `router_tokens_total` | counter | `route`, `model`, `key_id`, `key_name`, `kind` (`prompt`, `completion`) | Tokens reported by backends |
| `router_request_duration_seconds` | histogram | `route` | Arrival to end of the response, forwarded requests. Buckets up to 600 s |
| `router_time_to_first_byte_seconds` | histogram | `route` | Arrival to first body byte, forwarded requests |
| `router_in_flight_requests` | gauge | `route` | Requests being forwarded |
| `router_key_requests_in_flight` | gauge | – | Requests currently admitted, all keys |
| `router_rejections_total` | counter | `reason` | Requests answered without forwarding: `auth`, `unknown_model`, `wrong_endpoint`, `forbidden`, `rate`, `concurrency`, `unavailable`, `invalid_request`, `body_too_large`, `not_found`, `busy`, `shutting_down`, and on occasion `internal_error` and `client_closed` |
| `router_backend_failures_total` | counter | `route`, `backend`, `kind` | Failed attempts: `connect`, `timeout`, `disconnect`, `http_5xx`, `midstream`, `midstream_timeout` |
| `router_backend_up` | gauge | `route`, `backend` | Probe state per backend: 1 up, 0 down |
| `router_route_up` | gauge | `route`, `kind` (`route`, `upstream`) | 1 while the route has a backend that is not down |
| `router_probe_duration_seconds` | histogram | `backend`, `result` (`ok`, `fail`) | Duration of health probes |
| `router_models` | gauge | `state` (`known`, `available`) | Model ids |
| `router_keys_loaded` | gauge | – | Keys in the cache |
| `router_key_cache_age_seconds` | gauge | – | Seconds since the keys were last read from the database |
| `router_key_refresh_failures_total` | counter | – | Failed key refreshes |
| `router_database_up` | gauge | – | 1 while the last database operation succeeded |
| `router_usage_queue_depth` | gauge | – | Usage records waiting to be written |
| `router_usage_write_failures_total` | counter | – | Failed attempts to write a usage batch |
| `router_usage_records_dropped_total` | counter | – | Usage records lost |
| `router_config_reloads_total` | counter | `result` (`ok`, `error`) | Config reloads |
| `router_config_loaded_timestamp_seconds` | gauge | – | When the running config was loaded |
| `router_internal_errors_total` | counter | – | Unexpected errors inside the router |
| `router_build_info` | gauge | `version`, `git_sha` | Always 1 |

Worth an alert: `router_backend_up == 0`, `router_database_up == 0`, a growing
`router_key_cache_age_seconds`, any increase of `router_usage_records_dropped_total`,
`router_config_reloads_total{result="error"}` or `router_internal_errors_total`.

## Health checks and availability

Two signals, kept apart on purpose.

**Active probes decide availability.** Every backend of a route with a `health_path` gets a `GET` on that
path every `ROUTER_HEALTH_INTERVAL` seconds, with the route's backend key if it has one.

- A probe succeeds on any HTTP status below 500. It fails on a connect error, a timeout or a 5xx. A
  health path that answers 404 or 401 therefore does not take a working backend offline.
- A backend is down after `ROUTER_HEALTH_FAILS` failed probes in a row and up again after one success.
  The first probe of a backend is taken at face value: a backend that is dead when the router starts is
  down at once.
- One probe round runs before the API port opens. A failed connect on a real request triggers a probe
  at once, so a dead backend is noticed sooner than the interval.
- Probes have a connection pool of their own. However many requests are in flight, a probe gets through.
- A route with `health_path: null` is not probed and always counts as available.

A model id is **available** while its route has at least one backend that is not down. Then:

| | Available | Not available |
|---|---|---|
| `GET /v1/models` | listed | not listed |
| A request for the id | forwarded | 503 `model_unavailable` with `Retry-After: 30`, at once |
| `GET /v1/models/{id}` | 200 | 503 (404 is for ids the router has never seen) |
| `/health` | route `up` | route `down` |

A discovered id stays known for as long as its route is in the config, so a backend that goes down
yields 503, not 404.

**Passive marks only steer selection.** A failed request (connect error, timeout, broken connection, 5xx)
marks that backend for `ROUTER_UNHEALTHY_SECONDS`. Within a pool, unmarked backends are preferred; if all
are marked, all are used. A mark never makes a model unavailable. Otherwise one request that provokes a
backend 500 could take a model offline for everyone.

**`ROUTER_HEALTH_GATING=off`** is the escape hatch. Probes still run and `/health` and the metrics still
report them, but they no longer affect requests: every configured model is listed and every request is
forwarded, as in 2.x. Use it if gating takes a healthy backend offline at your site, and fix the
`health_path` afterwards.

**The public `/health`** always answers 200, from cached state. `status` is `healthy`, or `degraded`
when the database cannot be reached (requests still work, from the cached keys). It is for people and
dashboards. Point container and kubelet probes at `/livez`, which never depends on anything but the
process: an outage of the database or a backend must not restart the router.

```json
{"status": "healthy", "router": "model-orchestrator", "version": "3.0.0",
 "database": {"status": "healthy"},
 "routes": {"Demo-Chat": "up", "Demo-Embed": "up", "Demo-Whisper": "up"}}
```

## Database and migrations

Postgres 16 or 17. The database holds keys, usage, the route names (for the allowlist picker of the admin
website) and two tables of the admin website: its audit log and its revoked sessions.

- **At start the database must be there.** The router waits up to 60 seconds for a connection and exits
  if there is none, so that the supervisor starts it again.
- **At runtime it may go away.** Requests keep working from the cached keys, `/health` says `degraded`,
  key changes do not arrive and usage records are retried and then dropped.
- **Migrations are applied by the router at start**, in order, from `migrations/NNNN_name.sql`, each in
  one transaction, under an advisory lock, and recorded in `schema_migrations`. There is no separate
  migration step and no external tool. The database role needs the right to create and alter tables.
- **Migrations only add.** `0001` is the 2.x schema written with `IF NOT EXISTS`, so an existing 2.x
  database is adopted in place. A database migrated by a newer version than the running one produces a
  warning, not an error.

| Migration | Adds |
|---|---|
| `0001_baseline` | `routes`, `api_keys`, `usage_daily` (the 2.x schema) |
| `0002_key_extras` | `api_keys.max_concurrency`, `owner_email`, `created_by`, `notes` |
| `0003_usage_hourly` | `usage_hourly` |
| `0004_admin_audit` | `admin_audit`, written by the admin website |
| `0005_admin_revoked_sessions` | `admin_revoked_sessions`, written by the admin website when someone signs out |

Backups are not part of the router. Back the database up if keys and usage history matter to you.

## Deployment

One image for both targets. It runs as an unprivileged user (uid 10001), holds no secrets and keeps no
files. Ready-made files are in `deployment/`.

Rules that hold everywhere:

- **Publish port 8090 only.** Port 9095 is internal.
- **Probe `/livez`**, not `/health`.
- **One replica.**
- **Put a TLS proxy in front** that allows bodies up to `ROUTER_MAX_BODY_MB`, waits as long as
  `CHAT_READ_TIMEOUT_S` and does not buffer responses.
- **Give the container a writable `/tmp`** if its root filesystem is read-only: uploads above 1 MB are
  spooled there.
- **Keep the stop timeout above `ROUTER_SHUTDOWN_GRACE_SECONDS`**, so the router can end requests in
  flight itself before the container is killed. The default of 8 seconds fits Podman's stop timeout of
  10 seconds and Kubernetes' 30. To give long requests more time, raise both together: `StopTimeout=`
  and `TimeoutStopSec=` in the Quadlet unit, `terminationGracePeriodSeconds` on Kubernetes. The files
  in `deployment/` show it as a commented example.

### Podman Quadlet

Rootless Podman with systemd units, everything on loopback behind nginx.

| File | Unit |
|---|---|
| `orchestrator-db.container`, `.volume`, `db.env.example` | Postgres on `127.0.0.1:5432` |
| `model-orchestrator.container`, `router.env.example` | The router on `127.0.0.1:8091`, internal port `127.0.0.1:9095` |
| `admin-web.container`, `admin-web.env.example` | The admin website on `127.0.0.1:8600` |
| `nginx.container`, `nginx/orchestrator.conf.template` | TLS on the public ports: 8090 for the API, 8501 for the admin UI |
| `admin-ui.container`, `admin-ui.env.example` | The old Streamlit UI on `127.0.0.1:8502`, until it is retired |
| `keycloak.container`, `keycloak.env.example`, `keycloak/realm.json`, `nginx/keycloak.conf.template` | Optional: Keycloak on `127.0.0.1:8601` for signing in to the admin website, see below |
| `vpn-sidecar.container`, `vpn-sidecar-b.container` | Optional SOCKS proxies for routes that set `proxy:` |

```bash
mkdir -p ~/.config/model-orchestrator/{nginx,certs} ~/.config/containers/systemd

# 1. Route config. Check it before the first start.
cp config.example.yaml ~/.config/model-orchestrator/config.yaml
$EDITOR ~/.config/model-orchestrator/config.yaml

# 2. Environment files with the secrets, readable by you only.
for f in db router admin-web; do
  install -m 600 deployment/$f.env.example ~/.config/model-orchestrator/$f.env
  $EDITOR ~/.config/model-orchestrator/$f.env
done

# 3. TLS certificate and the nginx config.
cp deployment/nginx/orchestrator.conf.template ~/.config/model-orchestrator/nginx/
cp /path/to/server.crt ~/.config/model-orchestrator/certs/server.crt
install -m 600 /path/to/server.key ~/.config/model-orchestrator/certs/server.key

# 4. Units.
cp deployment/orchestrator-db.container deployment/orchestrator-db.volume \
   deployment/model-orchestrator.container deployment/admin-web.container \
   deployment/nginx.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start orchestrator-db model-orchestrator admin-web nginx

# 5. Check.
curl -s  http://127.0.0.1:9095/health        # the detailed view, on the host only
curl -sk https://localhost:8090/health       # the API through nginx
```

Then open `https://<host>:8501`, sign in and create the first key.

The units use `Image=...:latest` with `AutoUpdate=registry`. `latest` moves only when a release is
tagged, so with `podman-auto-update.timer` enabled a host follows releases unattended. To hold a version,
write `:vX.Y.Z` or `:sha-<short>` in the unit. `systemctl restart` alone never pulls a new image.

Every unit has its own `HealthCmd` and does not rely on the image's `HEALTHCHECK`: an image built with
Podman has none, because the OCI image format has no place for it.

#### Keycloak next to the router

For a site that has no identity provider to sign in to the admin website with. Keycloak runs as one
more unit on loopback, keeps its data in a second database of the router's Postgres, and gets its own
https port on the same nginx. The realm file creates a realm with registration off, the website's
client and the role `router-admin`; you create the accounts.

```bash
mkdir -p ~/.config/model-orchestrator/keycloak/import

# 1. A role and a database for Keycloak, once. Use the password you put in keycloak.env.
podman exec -it orchestrator-db psql -U orchestrator -d orchestrator \
  -c "CREATE ROLE keycloak LOGIN PASSWORD 'change-me'" -c "CREATE DATABASE keycloak OWNER keycloak"

# 2. Settings, the realm file and the nginx part.
install -m 600 deployment/keycloak.env.example ~/.config/model-orchestrator/keycloak.env
$EDITOR ~/.config/model-orchestrator/keycloak.env        # public address, passwords, realm name
cp deployment/keycloak/realm.json ~/.config/model-orchestrator/keycloak/import/
cp deployment/nginx/keycloak.conf.template ~/.config/model-orchestrator/nginx/
$EDITOR ~/.config/containers/systemd/nginx.container     # uncomment the four Keycloak lines

# 3. Start it. The first start takes about a minute.
cp deployment/keycloak.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start keycloak && systemctl --user restart nginx

# 4. The client secret for admin-web.env, and a first account with the role.
kc() { podman exec -it orchestrator-keycloak /opt/keycloak/bin/kcadm.sh "$@"; }
kc config credentials --server http://127.0.0.1:8601 --realm master --user bootstrap-admin
id=$(kc get clients -r <realm> -q clientId=model-orchestrator-admin --fields id --format csv --noquotes)
kc get clients/$id/client-secret -r <realm> --fields value
kc create users -r <realm> -s username=<name> -s enabled=true -s email=<address>
kc set-password -r <realm> --username <name> --temporary
kc add-roles -r <realm> --uusername <name> --rolename router-admin
```

Then set these in `admin-web.env` and restart `admin-web`:

```
ADMIN_OIDC_ISSUER=https://<public address>:<port>/realms/<realm>
ADMIN_OIDC_INTERNAL_URL=http://127.0.0.1:8601/realms/<realm>
ADMIN_OIDC_CLIENT_ID=model-orchestrator-admin
ADMIN_OIDC_CLIENT_SECRET=<from step 4>
ADMIN_OIDC_REQUIRED_ROLE=router-admin
```

Things to know:

- **The internal address** lets the website fetch Keycloak's settings, keys and tokens over loopback.
  Browsers use the public address. The website therefore does not have to be able to verify the public
  certificate, which matters where the certificate is one that browsers accept after a warning but
  programs reject (self-signed, or without a subjectAltName).
- **The administration console** is at `https://<public address>:<port>/admin/`. nginx lets only the
  addresses in `KEYCLOAK_ADMIN_FROM` reach it and the `master` realm; everyone else gets a 403 there and
  can still sign in to the other realms. The steps of point 4 can also be done in the console.
- **The first administrator** comes from `KC_BOOTSTRAP_ADMIN_*` and is meant to be temporary: create
  your own account in the realm `master`, give it the role `admin`, delete the bootstrap account and
  remove the two lines from `keycloak.env`.
- **The realm file is read once.** When the realm exists, later starts leave it alone, so changes are
  made in the console from then on. `${...}` in the file is filled in from `keycloak.env`.
- **No unattended updates.** The unit pins Keycloak's minor version and has no `AutoUpdate`. For patch
  releases pull the tag and restart the unit. Back up the `keycloak` database with the router's.

### Kubernetes

Templates in `deployment/kubernetes/`. They expect a Postgres you provide.

| File | Content |
|---|---|
| `namespace.yaml` | Namespace `model-orchestrator` |
| `configmap.yaml` | The route config |
| `secret.yaml.example` | Database URL, admin website secrets, backend keys |
| `deployment.yaml`, `service.yaml` | The router. Probes on `/livez`, the config mounted as a directory, port 9095 for scraping only |
| `admin-web.yaml` | The admin website, as a Deployment and Service of its own |
| `ingress.yaml.example` | One host for the API, one for the admin website |

```bash
kubectl apply -f deployment/kubernetes/namespace.yaml
$EDITOR deployment/kubernetes/configmap.yaml           # your routes
kubectl apply -f deployment/kubernetes/configmap.yaml

cp deployment/kubernetes/secret.yaml.example secret.yaml
$EDITOR secret.yaml                                    # do not commit this file
kubectl apply -f secret.yaml

$EDITOR deployment/kubernetes/deployment.yaml          # set the image tag you deploy
kubectl apply -f deployment/kubernetes/deployment.yaml -f deployment/kubernetes/service.yaml
kubectl apply -f deployment/kubernetes/admin-web.yaml

kubectl -n model-orchestrator port-forward svc/model-orchestrator 8090:8090 &
curl -s http://127.0.0.1:8090/health
```

Pin the image tags (`vX.Y.Z` or `sha-<short>`). To change routes, edit and apply the ConfigMap: the
kubelet updates the mounted file within a minute or two and the router reloads it by itself.

### TLS and the proxy in front

The router speaks plain HTTP. `deployment/nginx/orchestrator.conf.template` is a working nginx config for
both the API and the admin UI: 200 MB bodies, 600-second timeouts, no response buffering. If you change
`ROUTER_MAX_BODY_MB` or `CHAT_READ_TIMEOUT_S`, change the proxy to match.

Clients have to be able to verify the certificate. A certificate issued for an IP address needs that
address as a `subjectAltName`, and Python 3.13 and newer also reject a CA certificate without a
`keyUsage` extension. Python clients are stricter than `curl`, so test with both.

## Admin website

`admin_web/` is the web console for the router: create and manage keys, see usage and tokens per key,
route, model and outcome, check which routes are up, and read the audit log. It signs people in with
OpenID Connect (for example Keycloak) or a local account, uses the router's database and never changes
its schema.

Everything about it is in [`admin_web/README.md`](admin_web/README.md). The demo stack starts it on
<http://127.0.0.1:8600>.

`admin_ui/` is the Streamlit page of 2.x. It still works and stays deployable until the admin website has
replaced it everywhere.

## Smoke test for a deployed router

`scripts/smoke.py` checks a running router from the outside. Run it after every deploy.

```console
$ export SMOKE_API_KEY=sk-mo-...
$ uv run python scripts/smoke.py --base-url https://router.example.org
router: https://router.example.org
CHECK                MODEL         RESULT  TIME   DETAIL
health               -             ok      0.01s  status healthy, version 3.0.0, 6 of 6 route(s) up
auth                 -             ok      0.00s  a request without a key is rejected with 401
models               -             ok      0.00s  12 model id(s) on 6 route(s)
chat                 demo-chat     ok      0.41s  reasoning field: yes, 6 completion tokens
chat, stream         demo-chat     ok      0.38s  8 events, first byte after 0.12 s, reasoning field: yes
vision chat          demo-vision   ok      0.35s  reasoning field: yes, 6 completion tokens
vision chat, stream  demo-vision   ok      0.33s  8 events, first byte after 0.11 s, reasoning field: yes
embeddings           demo-embed    ok      0.02s  4 dimensions
rerank               demo-rerank   ok      0.02s  2 result(s) for top_n=2
voices               demo-kokoro   ok      0.01s  3 voice(s), using 'af_heart'
speech               demo-kokoro   ok      0.21s  296 bytes of audio/wav
transcription        demo-whisper  ok      0.30s  of the speech above: 'This is a smoke test.'
12 passed, 0 failed, 0 skipped
```

It reads `/health` and `/v1/models`, then makes one cheap call per route type the router offers, and
feeds the audio from text-to-speech into speech-to-text. A failed row names the status, the error code,
who produced the error and the request id to look for in the router's log. The exit code is 0 when
everything passed, 1 when a check failed, 2 when the test could not start.

| Option | Meaning |
|---|---|
| `--base-url URL` | The router. Also `SMOKE_BASE_URL` |
| `SMOKE_API_KEY` | The key, from the environment. `--api-key` works too, but ends up in the shell history |
| `--ca-file FILE` | Trust a private CA |
| `--insecure` | Do not verify the certificate, for deployments whose certificate clients cannot verify |
| `--all-routes` | Call one model of every route, not one per route type |
| `--model TYPE=ID` | Call this model for a type; repeatable |
| `--allow-down` | Do not fail because `/health` reports a route as down |
| `--timeout S`, `--max-tokens N`, `--voice NAME` | Per-call timeout (120 s), chat length (256), voice |
| `--json` | Machine-readable output |

The script needs only httpx and runs on its own: copy the file to another machine and
`uv run smoke.py --base-url ...` installs httpx for that run.

## Development and testing

Needs [uv](https://docs.astral.sh/uv/) (it installs Python 3.13) and, for the end-to-end tests, Podman or
a Postgres you point the suite at.

```bash
cd model-orchestrator
uv sync

uv run ruff check . && uv run ruff format --check .    # lint and format
uv run mypy                                            # types, strict
uv run pytest tests/unit                               # no network, no database, about a second
uv run pytest                                          # everything, about four minutes

uv run python -m model_orchestrator check-config config.example.yaml
ROUTER_DATABASE_URL=postgresql://... ROUTER_CONFIG=config.yaml uv run python -m model_orchestrator
```

| Layer | Where | What |
|---|---|---|
| Unit tests | `tests/unit/` | Config validation, limiter, event-stream splitting at every chunk boundary, usage extraction, error mapping, model table, health logic |
| Contract suite | `tests/e2e/` | The router as a black box over HTTP, against fake backends and a real Postgres. It is the definition of the client contract |
| Fake backends | `tests/fakes/` | Real HTTP servers that answer like vLLM, TEI, a reranker, Whisper and Kokoro, and can be told to fail |
| Image smoke test | CI | The built image starts against Postgres and answers |
| Live smoke script | `scripts/smoke.py` | For deployed routers, see above |

[`tests/README.md`](tests/README.md) explains the suite, its fixtures and the `ROUTER_TARGET` switch that
runs the same tests against the 2.x router.

Adding a pass-through endpoint is one entry in the `ENDPOINTS` table of `model_orchestrator/api.py`,
plus its tests. After changing dependencies in `pyproject.toml` run `uv lock`; the image installs exactly
what `uv.lock` pins.

## Releases and image tags

CI (`.github/workflows/model-orchestrator.yml`) runs on pull requests and on pushes to `main` that touch
`model-orchestrator/`. Changes to Markdown files alone do not trigger it. The router image is published
only after lint, type check, unit tests, the contract suite on Postgres 16 and 17, the image build and
the image smoke test have passed. A pull request builds the image without publishing it.

| Image | Release tag in git |
|---|---|
| `ghcr.io/knaeckebrothero/model-orchestrator` | `model-orchestrator-vX.Y.Z` |
| `ghcr.io/knaeckebrothero/model-orchestrator-admin-web` | `model-orchestrator-admin-web-vX.Y.Z` |
| `ghcr.io/knaeckebrothero/model-orchestrator-admin-ui` | `model-orchestrator-admin-ui-vX.Y.Z` |

| Image tag | Published |
|---|---|
| `sha-<short>` | On every build of `main`, and on a release |
| `vX.Y.Z` | On a release tag only |
| `latest` | On a release tag only |

`latest` never follows `main`. A deployment that updates itself from `latest` therefore changes only
when a release is cut on purpose, and work on `main` can be piloted elsewhere by pinning a `sha-` tag.

To release the router:

```bash
git tag model-orchestrator-v3.0.0 && git push origin model-orchestrator-v3.0.0
```

The version and commit of a running router are in `/health` (version), in the internal `/health`
(`version`, `git_sha`) and in `router_build_info`.

## Upgrading from 2.x

Version 3 keeps the client contract of 2.x except for the changes below, keeps every environment
variable and the route config schema, and adopts an existing database without a manual step. An upgrade
is an image change.

**Before you upgrade**

1. Run `check-config` from the new image on your live config. Validation is stricter (see below).
2. Look at the `health_path` of every route. Version 3 acts on what the probes say.
3. Make sure nothing publishes port 9095, and that nothing else on the host uses it.
4. If something scrapes `GET /metrics` on the API port or parses `/health`, change it first.

**What operators will notice**

| Area | 2.x | 3 |
|---|---|---|
| Internal port | – | A second listener, `ROUTER_METRICS_PORT` (default 9095), on the same address as the API: Prometheus `/metrics` and the detailed `/health`, without auth. Do not publish it. `0` switches it off |
| Health gating | `health_path` was for display only | Probes decide availability: a model whose backends are all down leaves `/v1/models` and answers 503. A probe succeeds on any status below 500. `ROUTER_HEALTH_GATING=off` restores the old behaviour |
| Migrations | The router created its tables | The router applies numbered migrations at start and records them in `schema_migrations`. They only add: new nullable columns on `api_keys`, the tables `usage_hourly` and `admin_audit`. The role needs to create and alter tables |
| Config validation | Unknown keys were ignored; a duplicate alias was won by the later route | Unknown keys, duplicate names and duplicate aliases stop the start. `check-config` tests a file beforehand |
| Config reload | Restart | The file is re-read on change and on `SIGHUP`. An invalid file is rejected and the running config stays |
| Container health check | `/health` | `/livez`. `/health` no longer probes backends on every call |
| Logs | Text lines while a request is handled; `DEBUG` printed body previews | One summary line per request with fixed fields, as text or JSON. Bodies are never logged, at any level |
| Image | `python main.py`, unpinned dependencies | `python -m model_orchestrator`, dependencies from `uv.lock`, uid 10001 |
| Image tags | `latest` followed `main` | `latest` moves on a release tag only |
| Usage | `usage_daily` | `usage_daily` unchanged, plus `usage_hourly` with tokens and outcomes |
| Shutdown | The process waited for requests in flight until the supervisor killed it | After `SIGTERM`, requests in flight get `ROUTER_SHUTDOWN_GRACE_SECONDS` (8) and are then ended with an answer. The stop timeout of the supervisor has to be longer than that |
| New settings | – | Per key: `max_concurrency`. Per route: `timeout_s`, `usage_from_stream`. Top level: `upstreams`. Environment: `ROUTER_UPSTREAM_MAX_CONNECTIONS`, `ROUTER_CLIENT_WRITE_TIMEOUT`, `ROUTER_DEFAULT_MAX_CONCURRENCY`, `ROUTER_SHUTDOWN_GRACE_SECONDS` and the ones for health, metrics, logs and reload in the [table above](#environment-variables) |

**What clients will notice**

Everything else behaves as before.

| Area | 2.x | 3 |
|---|---|---|
| Streaming errors | Always HTTP 200. A backend error gave an empty stream that ended cleanly | A failure before the first byte is the real HTTP status with a JSON error. A failure after that is an error event in the stream, then `[DONE]` |
| Backend errors | A backend 5xx became `503 All backends unavailable`, and error bodies were wrapped as `"Backend error: {...}"` | The backend's own status and message. `X-Router-Error-Source` says who produced an error |
| Timeouts | A read timeout became 503 after a second attempt | 504 `backend_timeout`, no second attempt. The connect timeout is 10 s on every endpoint |
| Dead backends | Still listed; requests failed after a connect timeout; a discovered id turned into 404 | Not listed; 503 `model_unavailable` with `Retry-After` at once; a discovered id that was seen before is 503, not 404 |
| Broken connections | Plain-text 500 without request id | 502 `backend_disconnected`, in the envelope |
| Pools | A stream got one backend and no retry | A stream is retried on another backend as long as nothing has reached the client |
| Abandoned requests | The backend kept generating | The backend call is cancelled |
| Error format | Unknown paths and wrong methods returned `{"detail": ...}`; some failures were plain-text 500 | Always the OpenAI envelope with `X-Request-ID`. `code` is set for the cases in the [error table](#errors). A body that is not a JSON object, or a `model` that is not a string, is a 400 |
| Rate-limit headers | On successful responses and on the 429 | On every response once the limit has been checked |
| Rerank | `top_n` was always dropped | `top_n` is forwarded. Only `null` is dropped |
| Speech-to-text | Only `file` and `model` were forwarded; a missing file was a 500 | All form fields are forwarded. A missing file is a 400. A missing `model` uses the first `stt` route |
| `/v1/models` | – | Entries gain `x_router`. `GET /v1/models/{id}` works for ids containing `/` |
| `/v1/audio/voices` | Asked the backend on every call | Cached for 5 minutes per route |
| `/health` | Backend URLs and the number of keys, to anyone | Status, version, database status, up or down per route name. The detail moved to the internal port |
| `GET /metrics` | JSON counters on the API port, for any key holder | Removed. Prometheus metrics on the internal port |
| `/docs`, `/redoc`, `/openapi.json` | Public | Off unless `ROUTER_EXPOSE_DOCS=1` |
| Large bodies | Any size; only the proxy in front limited it | 413 above `ROUTER_MAX_BODY_MB` (default 200) |
| Concurrency cap | – | 429 `concurrency_limit_exceeded` for keys that have a cap. Keys without one are unaffected, unless the operator sets a default cap |
| Stalled streams | – | A streaming client that reads nothing for 60 seconds is dropped |
| Overload | – | 503 `router_busy` with `Retry-After: 1` when the router has no free connection to the backends |
| Shutdown | The connection was cut | 503 `router_shutting_down`, or an error event with that code in a stream |

`roadmap.md` has the rollout plan and what comes after it.
