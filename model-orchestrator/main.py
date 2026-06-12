"""
AI Model Router - OpenAI API-compatible gateway
Routes requests to backend models based on the request's model field.

Transparent pass-through proxy: bodies are forwarded untouched apart from
rewriting `model` to the backend's name. Routing comes from the YAML config;
API keys (per-key rate limit, model allowlist, expiry) live in Postgres and
are managed via the Streamlit admin UI (see store.py and admin_ui/).
"""
import os
import sys
import json
import time
import uuid
import asyncio
import logging
import yaml
import httpx
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone
from contextvars import ContextVar
from collections import defaultdict
from contextlib import asynccontextmanager

import store

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

# ============ STRUCTURED LOGGING ============
# Per-request context propagated via contextvars. A middleware sets a fresh
# dict on every inbound request; handlers update it as they learn facts
# (owner, limit, model, backend). A logging filter + custom formatter then
# surface those fields on every log line without the caller having to thread
# them explicitly.
_log_context: ContextVar[Dict[str, str]] = ContextVar("_log_context", default={})


def set_log_context(**kwargs: str) -> None:
    """Update structured logging fields for the current request."""
    _log_context.get().update(kwargs)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _log_context.get()
        record.request_id = ctx.get("request_id", "-")
        record.key_owner = ctx.get("key_owner", "")
        record.limit = ctx.get("limit", "")
        record.model = ctx.get("model", "")
        record.backend = ctx.get("backend", "")
        return True


class _ContextFormatter(logging.Formatter):
    """Base line + key=value suffix for whichever context fields are populated."""
    _BASE = "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s"

    def __init__(self) -> None:
        super().__init__(self._BASE)

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = []
        for field in ("key_owner", "limit", "model", "backend"):
            val = getattr(record, field, "")
            if val:
                name = "owner" if field == "key_owner" else field
                extras.append(f"{name}={val}")
        return f"{base} {' '.join(extras)}" if extras else base


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_ContextFormatter())
_handler.addFilter(_ContextFilter())

logger = logging.getLogger("model-orchestrator")
logger.handlers = [_handler]
logger.propagate = False
logger.setLevel(os.environ.get("ROUTER_LOG_LEVEL", "INFO").upper())

# Httpx clients keyed by proxy URL. The default (direct) client is under key None;
# each distinct `proxy:` value from the config gets its own client so connection
# pools stay warm per upstream path.
http_clients: Dict[Optional[str], httpx.AsyncClient] = {}

def _make_client(proxy: Optional[str]) -> httpx.AsyncClient:
    kwargs: Dict[str, Any] = {
        "timeout": httpx.Timeout(120.0, connect=10.0),
        "limits": httpx.Limits(max_connections=1000, max_keepalive_connections=100),
        "http2": True,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)

def get_client(proxy: Optional[str]) -> httpx.AsyncClient:
    return http_clients.get(proxy) or http_clients[None]

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    http_clients[None] = _make_client(None)
    unique_proxies = {
        r.get("proxy") for r in config.get("routes", []) if r.get("proxy")
    }
    for proxy in unique_proxies:
        http_clients[proxy] = _make_client(proxy)
    logger.info(
        f"Router started, {len(http_clients)} httpx client(s) initialized "
        f"(proxies: {sorted(unique_proxies) or 'none'})"
    )
    await store.init(
        routes=[
            (r.get('name', ''), r.get('type', ''))
            for r in config.get('routes', []) if r.get('name')
        ],
    )
    await discover_and_rebuild()
    discovery_task = asyncio.create_task(_discovery_loop())
    try:
        yield
    finally:
        discovery_task.cancel()
        try:
            await discovery_task
        except asyncio.CancelledError:
            pass
        await store.close()
        for client in http_clients.values():
            await client.aclose()
        logger.info("Router shutdown complete")

# ============ CONFIGURATION ============
CONFIG_PATH = os.environ.get("ROUTER_CONFIG", "config.yaml")

def load_config() -> Dict[str, Any]:
    """Load the YAML config at ``ROUTER_CONFIG`` (default ``config.yaml``).

    The config holds ``server`` and ``routes`` only — API keys (and their
    rate limits / model allowlists) live in Postgres, managed through the
    admin UI. Schema reference lives in ``config.example.yaml``.
    """
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f) or {}

config = load_config()
logger.info(f"Config loaded: {len(config.get('routes', []))} routes")


# ============ LOAD BALANCING ============
# Per-route round-robin counters (keyed by route `name`) and a global
# unhealthy-until map keyed by backend URL. When a backend raises a
# connection/timeout error or returns 5xx, it's skipped for
# UNHEALTHY_DURATION seconds.
UNHEALTHY_DURATION = float(os.environ.get("ROUTER_UNHEALTHY_SECONDS", "30"))
_route_counters: Dict[str, int] = defaultdict(int)
_unhealthy_until: Dict[str, float] = {}


def _normalize_backends(backend: Union[str, List[str]]) -> List[str]:
    """Accept a string or a list; always return a list of backend URLs."""
    if isinstance(backend, list):
        return [b for b in backend if b]
    return [backend] if backend else []


def select_backend(route_name: str, backends: List[str]) -> str:
    """Round-robin select a healthy backend for this route.

    If every backend is currently flagged unhealthy, fall back to the full
    set — transient marks shouldn't hard-fail requests.
    """
    now = time.time()
    healthy = [b for b in backends if _unhealthy_until.get(b, 0) <= now]
    pool = healthy or backends
    idx = _route_counters[route_name]
    _route_counters[route_name] = idx + 1
    return pool[idx % len(pool)]


def mark_unhealthy(backend_url: str) -> None:
    _unhealthy_until[backend_url] = time.time() + UNHEALTHY_DURATION
    logger.warning(f"marked backend unhealthy for {UNHEALTHY_DURATION:.0f}s: {backend_url}")


# Pre-resolve any per-route backend API keys at load time so we warn once if
# the env var is missing rather than failing every request silently.
_route_api_keys: Dict[int, Optional[str]] = {}
for _i, _route in enumerate(config.get('routes', [])):
    _api_key_env = _route.get('backend_api_key_env')
    if _api_key_env:
        _resolved = os.environ.get(_api_key_env) or None
        if not _resolved:
            logger.warning(
                f"Route '{_route.get('name')}' references env var '{_api_key_env}' "
                f"but it is unset/empty; outbound requests will have no Authorization"
            )
        _route_api_keys[_i] = _resolved
    else:
        _route_api_keys[_i] = None

# `MODEL_ROUTES` is rebuilt by `discover_and_rebuild()` from the union of
#   1. configured aliases (`models:` list in each route — explicit, always
#      present, request payload's `model` is rewritten to `backend_model_name`
#      before forwarding), and
#   2. IDs the backend reports via its own `/v1/models` (passthrough — the
#      payload's `model` is left alone because it already matches what the
#      backend serves).
# Aliases win on collisions so explicit config beats discovery. Refresh runs
# every `ROUTER_DISCOVERY_INTERVAL` seconds (default 300).
MODEL_ROUTES: Dict[str, Dict[str, Any]] = {}

REFRESH_INTERVAL = float(os.environ.get("ROUTER_DISCOVERY_INTERVAL", "300"))


async def _fetch_upstream_models(
    backend: str, proxy: Optional[str], api_key: Optional[str]
) -> List[Dict[str, Any]]:
    """GET <backend>/v1/models, returning the `data:` list or [] on failure."""
    client = get_client(proxy)
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = await client.get(f"{backend}/v1/models", headers=headers, timeout=5.0)
        if resp.status_code != 200:
            logger.info(
                f"upstream /v1/models {backend} returned {resp.status_code}"
            )
            return []
        return resp.json().get('data', []) or []
    except Exception as e:
        logger.info(f"upstream /v1/models {backend} failed: {e}")
        return []


async def discover_and_rebuild() -> None:
    """Probe every route's backend `/v1/models`, then rebuild MODEL_ROUTES.

    Each route contributes its configured `models:` aliases AND every model
    id the backend reports via `/v1/models`. Aliases win on collisions, and
    the alias path rewrites `model:` to `backend_model_name`; the discovery
    path leaves `model:` alone (it already matches the backend's id).

    Best-effort: a backend that doesn't expose `/v1/models` (Kokoro,
    Whisper) just contributes no discovered ids — its aliases still work.
    Probes the first backend in load-balanced pools (assumed homogeneous).
    Each unique (backend, proxy, api_key) tuple is fetched once per cycle.
    """
    upstream_by_route: Dict[int, List[Dict[str, Any]]] = {}
    fetch_cache: Dict[Tuple[str, Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for i, route in enumerate(config.get('routes', [])):
        backends = _normalize_backends(route.get('backend', []))
        if not backends:
            upstream_by_route[i] = []
            continue
        proxy = route.get('proxy')
        api_key = _route_api_keys.get(i)
        cache_key = (backends[0], proxy, api_key)
        if cache_key not in fetch_cache:
            fetch_cache[cache_key] = await _fetch_upstream_models(*cache_key)
        upstream_by_route[i] = fetch_cache[cache_key]

    new_routes: Dict[str, Dict[str, Any]] = {}
    for i, route in enumerate(config.get('routes', [])):
        backends = _normalize_backends(route.get('backend', []))
        base = {
            'name': route.get('name', ''),
            'backends': backends,
            'type': route['type'],
            'endpoint': route['endpoint'],
            'proxy': route.get('proxy'),
            'backend_api_key': _route_api_keys.get(i),
            'owned_by': route.get('owned_by'),
            'request_defaults': route.get('request_defaults') or {},
        }
        upstream_entries = upstream_by_route.get(i, [])
        upstream_by_id: Dict[str, Dict[str, Any]] = {
            u['id']: u for u in upstream_entries if u.get('id')
        }

        # 1. Configured aliases — request payload's `model` is rewritten to
        #    backend_model_name before forwarding.
        backend_model_name = route.get('backend_model_name')
        for alias in route.get('models', []):
            effective = backend_model_name or alias
            entry = {
                **base,
                'effective_backend_name': effective,
                '_upstream_meta': upstream_by_id.get(effective),
            }
            new_routes[alias] = entry

        # 2. Discovered ids — passthrough, no rewrite. Skip ids already
        #    claimed by an alias above (alias wins on collision).
        for upstream_id, meta in upstream_by_id.items():
            if upstream_id in new_routes:
                continue
            new_routes[upstream_id] = {
                **base,
                'effective_backend_name': upstream_id,
                '_upstream_meta': meta,
            }

    global MODEL_ROUTES
    MODEL_ROUTES = new_routes
    logger.info(
        f"routes rebuilt: {len(new_routes)} model id(s): {sorted(new_routes.keys())}"
    )


async def _discovery_loop() -> None:
    """Re-run discovery on a warmup ladder, then settle into REFRESH_INTERVAL.

    The startup probe (called from `lifespan`) often runs before VPN sidecars
    finish bringing up their tunnels, so backends are unreachable for the
    first 10–60 seconds of pod life. Waiting REFRESH_INTERVAL (5 min default)
    before the next attempt would leave `/v1/models` returning bare stubs for
    that whole window. The warmup ladder retries quickly until backends come
    online, then drops to the slower steady-state cadence.
    """
    warmup_delays = (15, 30, 60, 120)
    for delay in warmup_delays:
        try:
            await asyncio.sleep(delay)
            await discover_and_rebuild()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"warmup discovery refresh failed: {e}")
    while True:
        try:
            await asyncio.sleep(REFRESH_INTERVAL)
            await discover_and_rebuild()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"periodic discovery refresh failed: {e}")


def _enriched_model_entry(model_id: str, route: Dict[str, Any]) -> Dict[str, Any]:
    """Build a `/v1/models` entry, passing the upstream entry through verbatim
    when available. Router-controlled fields (`id`, optional `owned_by`)
    override; everything else (max_model_len, permission, root, parent,
    created, …) flows through unchanged.
    """
    upstream = route.get('_upstream_meta')
    if upstream:
        entry = dict(upstream)
        entry['id'] = model_id
        if route.get('owned_by'):
            entry['owned_by'] = route['owned_by']
        return entry
    # Bare stub — backend has no /v1/models (Kokoro, Whisper, etc.).
    return {
        "id": model_id,
        "object": "model",
        "created": 1704067200,
        "owned_by": route.get('owned_by') or 'local',
        "permission": [],
        "root": model_id,
        "parent": None,
    }

# API keys live in Postgres (see store.py) — the YAML config only describes
# server + routes. Refuse to start without a database: there is no YAML
# fallback for auth, and an empty key table means deny-all, never anonymous.
if not store.enabled:
    logger.error(
        "ROUTER_DATABASE_URL is not set. API keys are managed in Postgres "
        "(see README and deployment/orchestrator-db.container); the router "
        "cannot authenticate requests without it."
    )
    sys.exit(1)

# Rate limiting — in-memory sliding 60s window per key (single instance).
# The limit itself (requests per minute) comes from the key's DB record.
rate_limit_tracker: Dict[str, list] = defaultdict(list)

def check_rate_limit(api_key: str, rpm: int) -> tuple[bool, int, int]:
    """Check if request is within rate limit. Returns (allowed, limit, remaining)."""
    current_time = time.time()
    window_start = current_time - 60

    tracker = rate_limit_tracker[api_key]
    tracker[:] = [(ts, count) for ts, count in tracker if ts > window_start]

    current_count = sum(count for ts, count in tracker)
    remaining = max(0, rpm - current_count)

    if current_count >= rpm:
        return False, rpm, 0

    tracker.append((current_time, 1))
    return True, rpm, remaining - 1

def get_reset_time(api_key: str) -> int:
    current_time = time.time()
    window_start = current_time - 60
    tracker = rate_limit_tracker[api_key]
    if not tracker:
        return int(current_time + 60)
    oldest = min(ts for ts, _ in tracker if ts > window_start)
    return int(oldest + 60)

# ============ METRICS ============
key_metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
    'request_count': 0, 'error_count': 0, 'last_active': None
})
model_metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
    'request_count': 0, 'total_latency_ms': 0.0, 'error_count': 0
})
system_metrics: Dict[str, Any] = {
    'total_requests': 0, 'total_errors': 0, 'uptime_start': time.time()
}

def record_metrics(
    api_key: Optional[str],
    model: str,
    latency_ms: float,
    is_error: bool = False,
    route_name: Optional[str] = None,
):
    """Record in-memory request metrics, and — when ``route_name`` is given —
    queue one record for the Postgres daily usage aggregate.

    ``route_name`` marks a *terminal* outcome for the request; intermediate
    failover attempts pass None so each request is counted exactly once in
    ``usage_daily`` while still showing up in the in-memory error counters.
    """
    system_metrics['total_requests'] += 1
    if is_error:
        system_metrics['total_errors'] += 1

    if api_key and store.lookup(api_key) is not None:
        key_metrics[api_key]['request_count'] += 1
        if is_error:
            key_metrics[api_key]['error_count'] += 1
        key_metrics[api_key]['last_active'] = time.time()

    model_metrics[model]['request_count'] += 1
    model_metrics[model]['total_latency_ms'] += latency_ms
    if is_error:
        model_metrics[model]['error_count'] += 1

    if api_key and route_name:
        store.record_usage(api_key, route_name, is_error)

# ============ AUTH ============
def validate_api_key(authorization: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Validate the Bearer token against the Postgres-backed key cache.

    Returns ``(is_valid, metadata)``. Invalid covers: missing/malformed
    header, unknown key, disabled key, and expired key.
    """
    if not authorization or not authorization.startswith('Bearer '):
        return False, None
    metadata = store.lookup(authorization[7:])
    if metadata is None:
        return False, None
    if not metadata.get('enabled', True):
        return False, metadata
    expires_at = metadata.get('expires_at')
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return False, metadata
    return True, metadata

def extract_api_key(authorization: Optional[str]) -> Optional[str]:
    """Extract raw API key from Authorization header."""
    if authorization and authorization.startswith('Bearer '):
        return authorization[7:]
    return None

def resolve_rpm(metadata: Optional[Dict[str, Any]]) -> int:
    """Requests-per-minute limit for a key, from its DB record."""
    return int((metadata or {}).get('rate_limit_rpm', 100))

def check_model_access(metadata: Optional[Dict[str, Any]], route: Dict[str, Any]) -> None:
    """Raise 403 unless the key's ``allowed_models`` covers this route.

    The allowlist stores route *names* (e.g. ``Qwen3-Reranker-8B``), not
    model ids — so every alias of a route is covered by one entry. An empty
    or NULL allowlist means the key may use every route.
    """
    allowed = (metadata or {}).get('allowed_models')
    if allowed and route.get('name') not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"API key is not allowed to use model route '{route.get('name')}'",
        )

# ============ GENERIC PROXY FUNCTION ============
def apply_request_defaults(payload: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    """Merge a route's ``request_defaults`` into the outbound payload in place.

    Scalar defaults use ``setdefault`` semantics — a value the client already
    sent always wins. Dict-valued defaults (e.g. ``chat_template_kwargs``) are
    shallow-merged so a route can add a key (``enable_thinking``) without
    dropping keys the client supplied; on a per-key collision the client still
    wins. A client value of the wrong shape (non-dict where the default is a
    dict) is left untouched.

    Used for backend quirks that need a per-request field with no server-side
    default — notably Gemma 4 reasoning, which only separates ``reasoning``
    from ``content`` when the request carries ``skip_special_tokens: false``.
    """
    for key, default_val in defaults.items():
        if isinstance(default_val, dict):
            client_val = payload.get(key)
            if isinstance(client_val, dict):
                payload[key] = {**default_val, **client_val}
            elif key not in payload:
                payload[key] = dict(default_val)
            # else: client sent a non-dict for a dict-default key — respect it
        else:
            payload.setdefault(key, default_val)


async def proxy_request(
    method: str,
    route: Dict[str, Any],
    api_key: Optional[str],
    model: str,
    rpm: int = 100,
    payload: Optional[Dict[str, Any]] = None,
    files: Optional[Dict] = None,
    data: Optional[Dict] = None,
    stream: bool = False,
    timeout: float = 120.0,
    endpoint_override: Optional[str] = None,
    rate_limit: bool = True,
) -> Response:
    """Forward a request to one of the route's backends with retry + failover.

    Non-streaming requests attempt up to 2 distinct backends, marking a
    backend unhealthy on ConnectError/TimeoutException/5xx. Streaming
    requests pick one backend with no failover — once bytes start flowing
    there's no safe way to retry. Auth lives outside this function; the
    per-key rate-limit check here (limit = ``rpm``, from the key's DB
    record) runs after auth has already succeeded in the handler, and is
    skipped when ``rate_limit=False`` — used by open, unauthenticated
    discovery calls like ``/v1/audio/voices``.

    If the route declares a ``proxy:``, the cached httpx client for that
    proxy URL is used; otherwise the direct client.

    ``endpoint_override`` replaces the route's configured ``endpoint`` for
    this one call — used to hit a sibling path on the same backend (e.g. a
    TTS route's ``/v1/audio/voices`` instead of its ``/v1/audio/speech``).
    """
    backends: List[str] = route['backends']
    endpoint: str = endpoint_override or route['endpoint']
    proxy: Optional[str] = route.get('proxy')
    route_name: str = route['name']
    client = get_client(proxy)

    # Merge any per-route request defaults into the outbound JSON payload
    # before forwarding — e.g. Gemma 4 needs skip_special_tokens=false so its
    # reasoning parser can split `reasoning` from `content` (vLLM has no
    # server-side default for that flag). Client-supplied values win; no-op for
    # routes without request_defaults and for file uploads (payload is None).
    if payload is not None:
        _defaults = route.get('request_defaults')
        if _defaults:
            apply_request_defaults(payload, _defaults)

    if not backends:
        raise HTTPException(
            status_code=503,
            detail=f"Route '{route_name}' has no backends configured",
        )

    # Rate limit (counted once regardless of retries). Skipped for open,
    # unauthenticated discovery calls (rate_limit=False), which carry no
    # per-key budget and so return no X-RateLimit-* headers.
    rate_headers: Dict[str, str] = {}
    if rate_limit:
        allowed, limit, remaining = check_rate_limit(api_key, rpm)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(get_reset_time(api_key)),
                },
            )
        rate_headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(get_reset_time(api_key)),
        }

    # Do NOT forward the client's auth to backends (Bug 4 fix). Let httpx set
    # Content-Type for file uploads; set it explicitly for JSON. If the route
    # declares its own upstream credential, inject that instead.
    headers = {} if files else {"Content-Type": "application/json"}
    backend_api_key: Optional[str] = route.get('backend_api_key')
    if backend_api_key:
        headers["Authorization"] = f"Bearer {backend_api_key}"

    def _debug_preview(backend_url: str) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        if payload is not None:
            preview = json.dumps(payload)
            if len(preview) > 500:
                preview = preview[:500] + "...<truncated>"
            logger.debug(f"-> {method} {backend_url} payload={preview}")
        else:
            logger.debug(f"-> {method} {backend_url} files={list((files or {}).keys())}")

    # --- Streaming path: one backend, no failover. ------------------------
    if stream:
        backend_host = select_backend(route_name, backends)
        backend_url = f"{backend_host}{endpoint}"
        set_log_context(backend=backend_url)
        _debug_preview(backend_url)
        start_time = time.time()

        async def event_stream_generator():
            try:
                async with client.stream(
                    method, backend_url, json=payload,
                    headers=headers, timeout=timeout,
                ) as backend_response:
                    latency_ms = (time.time() - start_time) * 1000
                    record_metrics(
                        api_key, model, latency_ms,
                        backend_response.status_code != 200,
                        route_name=route['name'],
                    )
                    if backend_response.status_code >= 500:
                        mark_unhealthy(backend_host)
                    if backend_response.status_code != 200:
                        err_body = {
                            "error": {
                                "message": f"Backend error: {backend_response.text[:500]}",
                                "type": _error_type(backend_response.status_code),
                            }
                        }
                        yield f"data: {json.dumps(err_body)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    # Stream raw bytes through unchanged. aiter_lines() strips
                    # newlines and drops blank lines, which destroys the \n\n
                    # event terminators SSE requires — strict parsers (OpenAI
                    # SDK) then buffer the whole stream and never decode it.
                    async for chunk in backend_response.aiter_raw():
                        if chunk:
                            yield chunk
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                mark_unhealthy(backend_host)
                record_metrics(api_key, model, 0, is_error=True, route_name=route['name'])
                err_body = {
                    "error": {"message": f"Backend unavailable: {e}", "type": "api_error"}
                }
                yield f"data: {json.dumps(err_body)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream_generator(),
            media_type="text/event-stream",
            headers=rate_headers,
        )

    # --- Non-streaming path: up to 2 attempts across distinct backends. ---
    attempts = min(2, len(backends))
    tried: set = set()
    last_err = ""

    for _ in range(attempts):
        available = [b for b in backends if b not in tried]
        if not available:
            break
        backend_host = select_backend(route_name, available)
        backend_url = f"{backend_host}{endpoint}"
        tried.add(backend_host)
        set_log_context(backend=backend_url)
        _debug_preview(backend_url)

        start_time = time.time()
        try:
            if files:
                response = await client.post(
                    backend_url, files=files, data=data, headers=headers, timeout=timeout,
                )
            else:
                response = await client.request(
                    method, backend_url, json=payload, headers=headers, timeout=timeout,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            mark_unhealthy(backend_host)
            last_err = f"{type(e).__name__}: {e}"
            continue

        latency_ms = (time.time() - start_time) * 1000
        logger.debug(f"<- {response.status_code} latency_ms={latency_ms:.0f}")

        if response.status_code >= 500:
            mark_unhealthy(backend_host)
            record_metrics(api_key, model, latency_ms, True)
            last_err = f"{response.status_code} {response.text[:200]}"
            continue

        record_metrics(
            api_key, model, latency_ms,
            response.status_code != 200,
            route_name=route['name'],
        )
        if response.status_code != 200:
            # 4xx — backend is saying the request is wrong; don't failover.
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Backend error: {response.text}",
            )

        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/json"),
            headers=rate_headers,
        )

    record_metrics(api_key, model, 0, is_error=True, route_name=route['name'])
    raise HTTPException(
        status_code=503,
        detail=f"All backends unavailable: {last_err}",
    )

# ============ FASTAPI APP ============
app = FastAPI(
    title="Model Orchestrator",
    description="OpenAI API-compatible gateway for multiple AI models",
    version="1.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """
    Create a fresh logging context per request. Honours an incoming
    X-Request-ID if the caller supplies one; otherwise generates a short id.
    On success the id is echoed back as X-Request-ID; on errors the
    exception handlers pick it up from request.state.request_id.
    """
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = _log_context.set({"request_id": rid})
    request.state.request_id = rid
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        _log_context.reset(token)


# ============ ERROR ENVELOPES ============
# All errors are returned in OpenAI's standard shape so clients written against
# the OpenAI SDK can parse them uniformly:
#   {"error": {"message": ..., "type": ..., "code": ...}}

def _error_type(status: int) -> str:
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_error"
    if status == 429:
        return "rate_limit_error"
    if 500 <= status < 600:
        return "api_error"
    return "invalid_request_error"


def openai_error_response(
    status: int,
    message: str,
    type_: Optional[str] = None,
    code: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    request: Optional[Request] = None,
) -> JSONResponse:
    body: Dict[str, Any] = {"message": message, "type": type_ or _error_type(status)}
    if code:
        body["code"] = code
    resp_headers = dict(headers or {})
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            resp_headers["X-Request-ID"] = rid
    return JSONResponse(status_code=status, content={"error": body}, headers=resp_headers)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(f"{exc.status_code} {str(exc.detail)[:200]}")
    return openai_error_response(
        status=exc.status_code,
        message=str(exc.detail),
        headers=exc.headers,
        request=request,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    msg = errs[0]["msg"] if errs else "Invalid request"
    logger.warning(f"422 {msg}")
    return openai_error_response(
        status=422,
        message=msg,
        type_="invalid_request_error",
        request=request,
    )


# Liveness/readiness probe — intentionally cheap. Kubelet readiness probes
# typically run with sub-second timeouts; ``/health`` walks every backend
# so a single stalled upstream blows through that budget and triggers a
# crashloop. Point ``livenessProbe`` and ``readinessProbe`` at ``/livez``;
# keep ``/health`` for operator-facing backend visibility.
@app.get("/livez")
async def liveness_check():
    """Return 200 as long as the router process is running."""
    return {"status": "ok"}


# Short per-backend probe timeout so ``/health`` total latency is bounded
# by the slowest backend (probes run in parallel) rather than 5s × N.
_HEALTH_PROBE_TIMEOUT = 2.0


async def _probe_backend(backend_url: str, probe_path: str, proxy: Optional[str]) -> str:
    try:
        probe_client = get_client(proxy)
        response = await probe_client.get(
            f"{backend_url}{probe_path}", timeout=_HEALTH_PROBE_TIMEOUT
        )
        return "healthy" if response.status_code == 200 else "unhealthy"
    except Exception:
        return "unavailable"


@app.get("/health")
async def health_check():
    """Report database and per-backend reachability.

    Backends: probes every unique upstream URL across all routes
    (load-balanced pools are expanded). Uses the route's proxy client when
    set so VPN-only backends are probed through the tunnel. Probe path is
    per-route via ``health_path``: absent defaults to ``/health``; explicit
    ``null`` or empty string skips probing (useful for backends like Kokoro
    TTS that don't expose a health endpoint). Status values: ``healthy``
    (200), ``unhealthy`` (non-200), ``unavailable`` (connect/timeout),
    ``skipped`` (probing disabled).

    Database: pings Postgres and reports the key-store state (cached key
    count, seconds since the last successful key refresh, usage-queue
    depth). The top-level ``status`` is ``healthy`` or — when the database
    is unreachable — ``degraded``: auth keeps working from the cached key
    set, but key changes are frozen and usage records are dropped.

    Always returns HTTP 200. Container healthchecks must not restart the
    router on a database blip (startup *requires* the DB, while a running
    router survives an outage on its cache), so degradation is encoded in
    the body, not the status code. All probes fan out in parallel via
    ``asyncio.gather``, so total latency is bounded by the slowest single
    probe. Not suitable for kubelet probes — use ``/livez`` for those.
    """
    plan: List[Tuple[str, str, Optional[str]]] = []
    seen: set = set()
    backend_status: Dict[str, str] = {}
    for route in config.get('routes', []):
        proxy = route.get('proxy')
        probe_path = route['health_path'] if 'health_path' in route else '/health'
        for backend_url in _normalize_backends(route.get('backend', [])):
            if backend_url in seen:
                continue
            seen.add(backend_url)
            if not probe_path:
                backend_status[backend_url] = "skipped"
                continue
            plan.append((backend_url, probe_path, proxy))

    db_health, results = await asyncio.gather(
        store.health(timeout=_HEALTH_PROBE_TIMEOUT),
        asyncio.gather(*(_probe_backend(url, path, proxy) for url, path, proxy in plan)),
    )
    for (url, _, _), status in zip(plan, results):
        backend_status[url] = status

    overall = "healthy" if db_health["status"] == "healthy" else "degraded"
    logger.debug(f"health check status={overall} db={db_health['status']} backends={backend_status}")
    return {
        "status": overall,
        "router": "model-orchestrator",
        "database": db_health,
        "backends": backend_status,
    }

# Metrics endpoint (now requires auth - Bug 7 fix)
@app.get("/metrics")
async def get_metrics(req: Request):
    """Get metrics - requires valid API key."""
    auth_header = req.headers.get("authorization")
    is_valid, _ = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    key_metrics_output = {}
    for api_key, metrics in key_metrics.items():
        owner = (store.lookup(api_key) or {}).get('owner', 'unknown')
        key_metrics_output[owner] = {
            'request_count': metrics['request_count'],
            'error_count': metrics['error_count'],
            'last_active': metrics['last_active']
        }

    model_metrics_output = {}
    for model_name, metrics in model_metrics.items():
        avg_latency = 0.0
        if metrics['request_count'] > 0:
            avg_latency = metrics['total_latency_ms'] / metrics['request_count']
        model_metrics_output[model_name] = {
            'request_count': metrics['request_count'],
            'avg_latency_ms': round(avg_latency, 2),
            'error_count': metrics['error_count']
        }

    return {
        'key_metrics': key_metrics_output,
        'model_metrics': model_metrics_output,
        'system_metrics': {
            'total_requests': system_metrics['total_requests'],
            'total_errors': system_metrics['total_errors'],
            'uptime_seconds': round(time.time() - system_metrics['uptime_start'], 2)
        }
    }

# OpenAI-compatible /v1/models endpoint
@app.get("/v1/models")
async def list_models():
    """List available models - OpenAI compatible (no auth required).

    Each entry is enriched with upstream metadata (max_model_len, real
    permission array, real created timestamp) when the backend exposes
    `/v1/models` and we successfully cached it at startup.
    """
    models = []
    for model_name, route in MODEL_ROUTES.items():
        models.append(_enriched_model_entry(model_name, route))
    return {"object": "list", "data": models}

@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    """Get specific model info (no auth required)."""
    if model_id not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail="Model not found")
    return _enriched_model_entry(model_id, MODEL_ROUTES[model_id])

# ============ ENDPOINTS WITH RAW BODY PASS-THROUGH ============

@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    """Proxy OpenAI-style chat completions to the backend for the requested model.

    Also handles vision requests (routes with ``type: vision``). The body is
    passed through largely untouched; only ``model`` is rewritten to the
    route's ``backend_model_name``. Supports streaming via ``stream: true``.

    Auth: Bearer token required (401 if invalid/disabled/expired).
    Rate limit: per-key rpm; exceeds → 429 with X-RateLimit-Reset.
    Status codes: 200 on success; 400 on non-chat model or invalid JSON;
    403 on model not allowed for this key; 404 on unknown model;
    429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid, disabled, or expired API key")

    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    rpm = resolve_rpm(key_metadata)
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, limit=f"{rpm}rpm")

    # Read raw body and extract model/stream (Bug 2 fix: pass-through)
    body = await req.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = payload.get('model')
    if not model or model not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")

    route = MODEL_ROUTES[model]
    if route['type'] not in ['chat', 'vision']:
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not a chat model")

    check_model_access(key_metadata, route)
    payload['model'] = route.get('effective_backend_name', model)

    stream = payload.get('stream', False)
    set_log_context(model=model)

    logger.info(f"chat request (stream={stream})")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        rpm=rpm,
        payload=payload,
        stream=stream,
    )

@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Proxy OpenAI-style embedding requests to the backend for the requested model.

    Auth: Bearer token required (401 if invalid/disabled/expired).
    Rate limit: per-key rpm; exceeds → 429.
    Status codes: 200 on success; 400 on non-embedding model or invalid JSON;
    403 on model not allowed for this key; 404 on unknown model;
    429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid, disabled, or expired API key")

    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    rpm = resolve_rpm(key_metadata)
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, limit=f"{rpm}rpm")

    body = await req.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = payload.get('model')
    if not model or model not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")

    route = MODEL_ROUTES[model]
    if route['type'] != 'embedding':
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not an embedding model")

    check_model_access(key_metadata, route)
    payload['model'] = route.get('effective_backend_name', model)
    set_log_context(model=model)

    logger.info("embeddings request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        rpm=rpm,
        payload=payload,
        timeout=60.0,
    )

@app.post("/v1/rerank")
async def rerank(req: Request):
    """Proxy reranking requests to the backend for the requested model.

    Strips ``top_n`` from the payload before forwarding (some backends crash
    when it's passed as ``null``).

    Auth: Bearer token required (401 if invalid/disabled/expired).
    Rate limit: per-key rpm; exceeds → 429.
    Status codes: 200 on success; 400 on non-reranker model or invalid JSON;
    403 on model not allowed for this key; 404 on unknown model;
    429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid, disabled, or expired API key")

    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    rpm = resolve_rpm(key_metadata)
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, limit=f"{rpm}rpm")

    body = await req.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = payload.get('model')
    if not model or model not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")

    route = MODEL_ROUTES[model]
    if route['type'] != 'reranker':
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not a reranker model")

    check_model_access(key_metadata, route)
    payload['model'] = route.get('effective_backend_name', model)
    # Remove top_n if present to prevent crash (pop is safe - no KeyError)
    payload.pop('top_n', None)

    set_log_context(model=model)

    logger.info("rerank request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        rpm=rpm,
        payload=payload,
        timeout=60.0,
    )

@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(req: Request):
    """Proxy Whisper-style audio transcription requests (multipart/form-data).

    The uploaded audio file is forwarded as-is; only the ``model`` form field
    is rewritten to the route's ``backend_model_name``. Defaults to
    ``whisper-large-v3-turbo`` when no model is specified.

    Auth: Bearer token required (401 if invalid/disabled/expired).
    Rate limit: per-key rpm; exceeds → 429.
    Status codes: 200 on success; 400 on non-STT model; 403 on model not
    allowed for this key; 404 on unknown model; 429 on rate limit;
    5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid, disabled, or expired API key")

    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    rpm = resolve_rpm(key_metadata)
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, limit=f"{rpm}rpm")

    form = await req.form()
    model_name = form.get("model", "whisper-large-v3-turbo")

    if model_name not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    route = MODEL_ROUTES[model_name]
    if route['type'] != 'stt':
        raise HTTPException(status_code=400, detail=f"Model '{model_name}' is not a speech-to-text model")

    check_model_access(key_metadata, route)
    file = form.get("file")
    file_bytes = await file.read()
    filename = file.filename or "audio.wav"
    content_type = file.content_type or "audio/wav"

    backend_model = route.get('effective_backend_name', model_name)
    files = {'file': (filename, file_bytes, content_type)}
    data = {"model": backend_model}

    set_log_context(model=model_name)

    logger.info("stt request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model_name,
        rpm=rpm,
        files=files,
        data=data,
        timeout=300.0,
    )

@app.post("/v1/audio/speech")
async def audio_speech(req: Request):
    """Proxy OpenAI-style text-to-speech requests to the backend for the requested model.

    Auth: Bearer token required (401 if invalid/disabled/expired).
    Rate limit: per-key rpm; exceeds → 429.
    Status codes: 200 on success (audio bytes in response body); 400 on
    non-TTS model or invalid JSON; 403 on model not allowed for this key;
    404 on unknown model; 429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid, disabled, or expired API key")

    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    rpm = resolve_rpm(key_metadata)
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, limit=f"{rpm}rpm")

    body = await req.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = payload.get('model')
    if not model or model not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")

    route = MODEL_ROUTES[model]
    if route['type'] != 'tts':
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not a text-to-speech model")

    check_model_access(key_metadata, route)
    payload['model'] = route.get('effective_backend_name', model)
    set_log_context(model=model)

    logger.info("tts request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        rpm=rpm,
        payload=payload,
        timeout=60.0,
    )

@app.get("/v1/audio/voices")
async def audio_voices(model: Optional[str] = None):
    """List the voices a text-to-speech backend offers, by forwarding to it.

    This is a kokoro-fastapi extension, not part of the OpenAI spec — the
    router keeps no voice list of its own, so it proxies the request to a
    ``type: tts`` backend's own ``/v1/audio/voices`` and returns that JSON
    unchanged. With a single TTS backend no parameters are needed; pass
    ``?model=<alias>`` to target a specific TTS route when more than one
    exists (otherwise the first configured TTS route is used).

    Open discovery endpoint, like ``/v1/models``: no auth and no rate limit
    (the upstream voice list isn't sensitive), so no X-RateLimit-* headers.
    Status codes: 200 on success; 400 when the named model isn't a TTS
    model; 404 when the model is unknown or no TTS backend is configured;
    5xx on backend failure.
    """
    if model is not None:
        if model not in MODEL_ROUTES:
            raise HTTPException(status_code=404, detail=f"Model '{model}' not found")
        route = MODEL_ROUTES[model]
        if route['type'] != 'tts':
            raise HTTPException(status_code=400, detail=f"Model '{model}' is not a text-to-speech model")
    else:
        route = next((r for r in MODEL_ROUTES.values() if r['type'] == 'tts'), None)
        if route is None:
            raise HTTPException(status_code=404, detail="No text-to-speech backend is configured")
        model = route['name']

    set_log_context(model=model)
    logger.info("tts voices request")

    return await proxy_request(
        method="GET",
        route=route,
        api_key=None,
        model=model,
        endpoint_override="/v1/audio/voices",
        rate_limit=False,
        timeout=30.0,
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("ROUTER_PORT", "8090"))
    # ROUTER_HOST env wins over the config file — lets the quadlet pin the
    # router to 127.0.0.1 behind the nginx TLS terminator without a second
    # config file, while k8s keeps 0.0.0.0 from the ConfigMap.
    host = os.environ.get("ROUTER_HOST") or config.get('server', {}).get('host', '0.0.0.0')
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
