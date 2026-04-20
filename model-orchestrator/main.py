"""
AI Model Router - OpenAI API-compatible gateway
Routes requests to 6 backend models based on model field

Rewritten as transparent pass-through proxy (~300 lines)
Fixes: reranker crash, dropped fields, auth headers, logging, boilerplate
"""
import os
import sys
import json
import time
import uuid
import logging
import yaml
import httpx
from typing import Dict, Any, List, Optional, Union
from contextvars import ContextVar
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

# ============ STRUCTURED LOGGING ============
# Per-request context propagated via contextvars. A middleware sets a fresh
# dict on every inbound request; handlers update it as they learn facts
# (owner, tier, model, backend). A logging filter + custom formatter then
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
        record.tier = ctx.get("tier", "")
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
        for field in ("key_owner", "tier", "model", "backend"):
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
    yield
    for client in http_clients.values():
        await client.aclose()
    logger.info("Router shutdown complete")

# ============ CONFIGURATION ============
CONFIG_PATH = os.environ.get("ROUTER_CONFIG", "config.yaml")
# Optional second YAML file holding only `api_keys:`. Lets k8s deployments put
# the main config in a ConfigMap and the keys in a Secret; its api_keys list
# is appended to whatever's in the main config.
KEYS_CONFIG_PATH = os.environ.get("ROUTER_KEYS_CONFIG")

def load_config() -> Dict[str, Any]:
    """Load the YAML config at ``ROUTER_CONFIG`` and optionally merge API keys.

    The main config (``ROUTER_CONFIG``, default ``config.yaml``) holds
    ``server``, ``tiers``, ``routes``, and optionally ``api_keys``. If
    ``ROUTER_KEYS_CONFIG`` is set, that file is loaded as well and its
    ``api_keys`` list is appended to whatever is already in the main config.

    This split is what lets a Kubernetes deployment keep the routing config
    in a ConfigMap and the keys in a Secret; Quadlet users can ignore
    ``ROUTER_KEYS_CONFIG`` and keep everything in one file.

    Returns the merged config dict. Schema reference lives in
    ``config.example.yaml``.
    """
    with open(CONFIG_PATH, 'r') as f:
        cfg = yaml.safe_load(f) or {}

    if KEYS_CONFIG_PATH:
        with open(KEYS_CONFIG_PATH, 'r') as f:
            keys_cfg = yaml.safe_load(f) or {}
        extra_keys = keys_cfg.get('api_keys', [])
        if extra_keys:
            cfg.setdefault('api_keys', []).extend(extra_keys)
            logger.info(f"Merged {len(extra_keys)} api_keys from {KEYS_CONFIG_PATH}")

    return cfg

config = load_config()
logger.info(f"Config loaded: {len(config.get('routes', []))} routes, {len(config.get('api_keys', []))} API keys")


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


# Build model-to-route mapping. `backends` is always a list (the single
# `backend:` string case is normalized on load).
MODEL_ROUTES: Dict[str, Dict[str, Any]] = {}
for route in config.get('routes', []):
    for model_name in route.get('models', []):
        MODEL_ROUTES[model_name] = {
            'name': route.get('name', model_name),
            'backends': _normalize_backends(route.get('backend', [])),
            'type': route['type'],
            'endpoint': route['endpoint'],
            'backend_model_name': route.get('backend_model_name', model_name),
            'proxy': route.get('proxy'),
        }

logger.info(f"Routes configured: {list(MODEL_ROUTES.keys())}")

# API keys
API_KEYS: Dict[str, Dict[str, Any]] = {}
for api_key_entry in config.get('api_keys', []):
    key = api_key_entry.get('key', '')
    if key:
        API_KEYS[key] = {
            'owner': api_key_entry.get('owner', 'unknown'),
            'tier': api_key_entry.get('tier', 'standard'),
            'enabled': api_key_entry.get('enabled', True),
            'name': api_key_entry.get('name', 'Unnamed')
        }

# Rate limiting
TIER_CONFIG = config.get('tiers', {})
rate_limit_tracker: Dict[str, list] = defaultdict(list)

def get_tier_limits(tier: str) -> Dict[str, Any]:
    return TIER_CONFIG.get(tier, TIER_CONFIG.get('standard', {
        'requests_per_minute': 100, 'burst': 20, 'priority': 2
    }))

def check_rate_limit(api_key: str, tier: str) -> tuple[bool, int, int]:
    """Check if request is within rate limit. Returns (allowed, limit, remaining)."""
    tier_limits = get_tier_limits(tier)
    rpm = tier_limits.get('requests_per_minute', 100)
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

def record_metrics(api_key: Optional[str], model: str, latency_ms: float, is_error: bool = False):
    """Record request metrics."""
    system_metrics['total_requests'] += 1
    if is_error:
        system_metrics['total_errors'] += 1

    if api_key and api_key in key_metrics:
        key_metrics[api_key]['request_count'] += 1
        if is_error:
            key_metrics[api_key]['error_count'] += 1
        key_metrics[api_key]['last_active'] = time.time()

    model_metrics[model]['request_count'] += 1
    model_metrics[model]['total_latency_ms'] += latency_ms
    if is_error:
        model_metrics[model]['error_count'] += 1

# ============ AUTH ============
def validate_api_key(authorization: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Validate API key from Authorization header."""
    if not API_KEYS:
        return True, None
    if not authorization:
        return False, None
    if authorization.startswith('Bearer '):
        token = authorization[7:]
        if token in API_KEYS:
            metadata = API_KEYS[token]
            if not metadata.get('enabled', True):
                return False, metadata
            return True, metadata
    return False, None

def extract_api_key(authorization: Optional[str]) -> Optional[str]:
    """Extract raw API key from Authorization header."""
    if authorization and authorization.startswith('Bearer '):
        return authorization[7:]
    return None

# ============ GENERIC PROXY FUNCTION ============
async def proxy_request(
    method: str,
    route: Dict[str, Any],
    api_key: Optional[str],
    model: str,
    tier: str,
    payload: Optional[Dict[str, Any]] = None,
    files: Optional[Dict] = None,
    data: Optional[Dict] = None,
    stream: bool = False,
    timeout: float = 120.0,
) -> Response:
    """Forward a request to one of the route's backends with retry + failover.

    Non-streaming requests attempt up to 2 distinct backends, marking a
    backend unhealthy on ConnectError/TimeoutException/5xx. Streaming
    requests pick one backend with no failover — once bytes start flowing
    there's no safe way to retry. Auth/rate limit live outside this function;
    the rate-limit check here enforces per-key budgets after auth has
    already succeeded in the handler.

    If the route declares a ``proxy:``, the cached httpx client for that
    proxy URL is used; otherwise the direct client.
    """
    backends: List[str] = route['backends']
    endpoint: str = route['endpoint']
    proxy: Optional[str] = route.get('proxy')
    route_name: str = route['name']
    client = get_client(proxy)

    if not backends:
        raise HTTPException(
            status_code=503,
            detail=f"Route '{route_name}' has no backends configured",
        )

    # Rate limit (counted once regardless of retries).
    allowed, limit, remaining = check_rate_limit(api_key, tier)
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

    # Do NOT forward auth to backends (Bug 4 fix). Let httpx set Content-Type
    # for file uploads; set it explicitly for JSON.
    headers = {} if files else {"Content-Type": "application/json"}

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
                    headers={"Content-Type": "application/json"}, timeout=timeout,
                ) as backend_response:
                    latency_ms = (time.time() - start_time) * 1000
                    record_metrics(api_key, model, latency_ms, backend_response.status_code != 200)
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
                    async for line in backend_response.aiter_lines():
                        if line:
                            yield line + "\n"
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                mark_unhealthy(backend_host)
                record_metrics(api_key, model, 0, is_error=True)
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

        record_metrics(api_key, model, latency_ms, response.status_code != 200)
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

    record_metrics(api_key, model, 0, is_error=True)
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


# Health check
@app.get("/health")
async def health_check():
    """Report per-backend reachability.

    Probes every unique upstream URL across all routes (load-balanced
    pools are expanded). Uses the route's proxy client when set so VPN-only
    backends are probed through the tunnel. Status is "healthy" (/health
    returned 200), "unhealthy" (non-200 response), or "unavailable"
    (connection failed). Router itself always reports healthy.
    """
    backend_status: Dict[str, str] = {}
    for route in config.get('routes', []):
        proxy = route.get('proxy')
        for backend_url in _normalize_backends(route.get('backend', [])):
            if backend_url in backend_status:
                continue
            try:
                probe_client = get_client(proxy)
                response = await probe_client.get(f"{backend_url}/health", timeout=5.0)
                backend_status[backend_url] = "healthy" if response.status_code == 200 else "unhealthy"
            except Exception:
                backend_status[backend_url] = "unavailable"

    logger.debug(f"health check backends={backend_status}")
    return {"status": "healthy", "router": "model-orchestrator", "backends": backend_status}

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
        owner = API_KEYS.get(api_key, {}).get('owner', 'unknown')
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
    """List available models - OpenAI compatible (no auth required)."""
    models = []
    for route in config.get('routes', []):
        for model_name in route.get('models', []):
            models.append({
                "id": model_name,
                "object": "model",
                "created": 1704067200,
                "owned_by": route.get('owned_by', 'local'),
                "permission": [],
                "root": model_name,
                "parent": None
            })

    return {"object": "list", "data": models}

@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    """Get specific model info (no auth required)."""
    if model_id not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail="Model not found")

    return {
        "id": model_id,
        "object": "model",
        "created": 1704067200,
        "owned_by": "local",
        "permission": [],
        "root": model_id,
        "parent": None
    }

# ============ ENDPOINTS WITH RAW BODY PASS-THROUGH ============

@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    """Proxy OpenAI-style chat completions to the backend for the requested model.

    Also handles vision requests (routes with ``type: vision``). The body is
    passed through largely untouched; only ``model`` is rewritten to the
    route's ``backend_model_name``. Supports streaming via ``stream: true``.

    Auth: Bearer token required (401 if invalid/disabled).
    Rate limit: per-tier; exceeds → 429 with X-RateLimit-Reset.
    Status codes: 200 on success; 400 on non-chat model or invalid JSON;
    404 on unknown model; 429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, tier=tier)

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

    # Replace model name with backend_model_name
    payload['model'] = route.get('backend_model_name', model)

    stream = payload.get('stream', False)
    set_log_context(model=model)

    logger.info(f"chat request (stream={stream})")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        stream=stream,
    )

@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Proxy OpenAI-style embedding requests to the backend for the requested model.

    Auth: Bearer token required (401 if invalid/disabled).
    Rate limit: per-tier; exceeds → 429.
    Status codes: 200 on success; 400 on non-embedding model or invalid JSON;
    404 on unknown model; 429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, tier=tier)

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

    payload['model'] = route.get('backend_model_name', model)
    set_log_context(model=model)

    logger.info("embeddings request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        timeout=60.0,
    )

@app.post("/v1/rerank")
async def rerank(req: Request):
    """Proxy reranking requests to the backend for the requested model.

    Strips ``top_n`` from the payload before forwarding (some backends crash
    when it's passed as ``null``).

    Auth: Bearer token required (401 if invalid/disabled).
    Rate limit: per-tier; exceeds → 429.
    Status codes: 200 on success; 400 on non-reranker model or invalid JSON;
    404 on unknown model; 429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, tier=tier)

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

    # Bug 1 fix: Use exclude_none to prevent top_n: null serialization
    payload['model'] = route.get('backend_model_name', model)
    # Remove top_n if present to prevent crash (pop is safe - no KeyError)
    payload.pop('top_n', None)

    set_log_context(model=model)

    logger.info("rerank request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        timeout=60.0,
    )

@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(req: Request):
    """Proxy Whisper-style audio transcription requests (multipart/form-data).

    The uploaded audio file is forwarded as-is; only the ``model`` form field
    is rewritten to the route's ``backend_model_name``. Defaults to
    ``whisper-large-v3-turbo`` when no model is specified.

    Auth: Bearer token required (401 if invalid/disabled).
    Rate limit: per-tier; exceeds → 429.
    Status codes: 200 on success; 400 on non-STT model; 404 on unknown model;
    429 on rate limit; 5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, tier=tier)

    form = await req.form()
    model_name = form.get("model", "whisper-large-v3-turbo")

    if model_name not in MODEL_ROUTES:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    route = MODEL_ROUTES[model_name]
    if route['type'] != 'stt':
        raise HTTPException(status_code=400, detail=f"Model '{model_name}' is not a speech-to-text model")

    file = form.get("file")
    file_bytes = await file.read()
    filename = file.filename or "audio.wav"
    content_type = file.content_type or "audio/wav"

    backend_model = route.get('backend_model_name', model_name)
    files = {'file': (filename, file_bytes, content_type)}
    data = {"model": backend_model}

    set_log_context(model=model_name)

    logger.info("stt request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model_name,
        tier=tier,
        files=files,
        data=data,
        timeout=300.0,
    )

@app.post("/v1/audio/speech")
async def audio_speech(req: Request):
    """Proxy OpenAI-style text-to-speech requests to the backend for the requested model.

    Auth: Bearer token required (401 if invalid/disabled).
    Rate limit: per-tier; exceeds → 429.
    Status codes: 200 on success (audio bytes in response body); 400 on
    non-TTS model or invalid JSON; 404 on unknown model; 429 on rate limit;
    5xx on backend failure.
    """
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    owner = key_metadata.get('owner') if key_metadata else 'unknown'
    api_key = extract_api_key(auth_header)
    set_log_context(key_owner=owner, tier=tier)

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

    payload['model'] = route.get('backend_model_name', model)
    set_log_context(model=model)

    logger.info("tts request")

    return await proxy_request(
        method="POST",
        route=route,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        timeout=60.0,
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("ROUTER_PORT", "8090"))
    # Bind to specific IP (Bug 3 fix: config not read - now reads from config)
    host = config.get('server', {}).get('host', '0.0.0.0')
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
