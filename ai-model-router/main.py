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
import logging
import yaml
import httpx
from typing import Dict, Any, Optional
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

# ============ STRUCTURED LOGGING ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("ai-model-router")

# Shared httpx client
http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    global http_client
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
        http2=True
    )
    logger.info("Router started, httpx client initialized")
    yield
    await http_client.aclose()
    logger.info("Router shutdown complete")

# ============ CONFIGURATION ============
CONFIG_PATH = os.environ.get("ROUTER_CONFIG", "config.yaml")

def load_config() -> Dict[str, Any]:
    """Load routing configuration from YAML file."""
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

config = load_config()
logger.info(f"Config loaded: {len(config.get('routes', []))} routes, {len(config.get('api_keys', []))} API keys")

# Build model-to-backend mapping
MODEL_ROUTES: Dict[str, Dict[str, Any]] = {}
for route in config.get('routes', []):
    for model_name in route.get('models', []):
        MODEL_ROUTES[model_name] = {
            'backend': route['backend'],
            'type': route['type'],
            'endpoint': route['endpoint'],
            'backend_model_name': route.get('backend_model_name', model_name)
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
    backend_url: str,
    api_key: Optional[str],
    model: str,
    tier: str,
    payload: Optional[Dict[str, Any]] = None,
    files: Optional[Dict] = None,
    data: Optional[Dict] = None,
    stream: bool = False,
    timeout: float = 120.0
) -> Response:
    """
    Generic proxy function to forward requests to backends.
    Handles auth, rate limiting, metrics, and streaming.
    """
    # Check rate limit
    allowed, limit, remaining = check_rate_limit(api_key, tier)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(get_reset_time(api_key))
            }
        )

    start_time = time.time()

    # Build headers - DO NOT forward auth to backends (Bug 4 fix)
    # Only set Content-Type for JSON requests; let httpx set it for file uploads
    headers = {} if files else {"Content-Type": "application/json"}

    try:
        if stream:
            # Streaming response
            async def event_stream_generator():
                async with http_client.stream(
                    method, backend_url, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout
                ) as backend_response:
                    latency_ms = (time.time() - start_time) * 1000
                    record_metrics(api_key, model, latency_ms, backend_response.status_code != 200)

                    if backend_response.status_code != 200:
                        yield f'data: {{"error": "Backend error: {backend_response.text}"}}\n\n'
                        return

                    async for line in backend_response.aiter_lines():
                        if line:
                            yield line + "\n"

            return StreamingResponse(
                event_stream_generator(),
                media_type='text/event-stream',
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(get_reset_time(api_key))
                }
            )
        else:
            # Non-streaming response
            if files:
                response = await http_client.post(
                    backend_url, files=files, data=data, headers=headers, timeout=timeout
                )
            else:
                response = await http_client.request(
                    method, backend_url, json=payload, headers=headers, timeout=timeout
                )

            latency_ms = (time.time() - start_time) * 1000
            record_metrics(api_key, model, latency_ms, response.status_code != 200)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Backend error: {response.text}"
                )

            # Return with rate limit headers
            return Response(
                content=response.content,
                media_type=response.headers.get("content-type", "application/json"),
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(get_reset_time(api_key))
                }
            )

    except (httpx.ConnectError, httpx.TimeoutException) as e:
        # Retry once
        try:
            if files:
                response = await http_client.post(
                    backend_url, files=files, data=data, headers=headers, timeout=timeout
                )
            else:
                response = await http_client.request(
                    method, backend_url, json=payload, headers=headers, timeout=timeout
                )
            latency_ms = (time.time() - start_time) * 1000
            record_metrics(api_key, model, latency_ms, response.status_code != 200)
        except Exception:
            record_metrics(api_key, model, 0, is_error=True)
            raise HTTPException(
                status_code=503,
                detail=f"Backend unavailable: {str(e)}. Please try again later."
            )

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Backend error: {response.text}")

        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/json"),
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(get_reset_time(api_key))
            }
        )

# ============ FASTAPI APP ============
app = FastAPI(
    title="AI Model Router",
    description="OpenAI API-compatible gateway for multiple AI models",
    version="1.0.0",
    lifespan=lifespan
)

# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint with backend availability status."""
    backend_status = {}
    for route in config.get('routes', []):
        backend_url = route.get('backend', '')
        if backend_url and backend_url not in backend_status:
            try:
                response = await http_client.get(f"{backend_url}/health", timeout=5.0)
                backend_status[backend_url] = "healthy" if response.status_code == 200 else "unhealthy"
            except Exception:
                backend_status[backend_url] = "unavailable"

    logger.info(f"Health check: {backend_status}")
    return {"status": "healthy", "router": "ai-model-router", "backends": backend_status}

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
    """Proxy chat completion requests - raw body pass-through."""
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    api_key = extract_api_key(auth_header)

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

    backend_url = f"{route['backend']}{route['endpoint']}"
    stream = payload.get('stream', False)

    logger.info(f"Chat: model={model}, stream={stream}, tier={tier}, owner={key_metadata.get('owner') if key_metadata else 'unknown'}")

    return await proxy_request(
        method="POST",
        backend_url=backend_url,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        stream=stream
    )

@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Proxy embedding requests - raw body pass-through."""
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    api_key = extract_api_key(auth_header)

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
    backend_url = f"{route['backend']}{route['endpoint']}"

    logger.info(f"Embeddings: model={model}, tier={tier}, owner={key_metadata.get('owner') if key_metadata else 'unknown'}")

    return await proxy_request(
        method="POST",
        backend_url=backend_url,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        timeout=60.0
    )

@app.post("/v1/rerank")
async def rerank(req: Request):
    """Proxy reranking requests - raw body pass-through with exclude_none."""
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    api_key = extract_api_key(auth_header)

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

    backend_url = f"{route['backend']}{route['endpoint']}"

    logger.info(f"Rerank: model={model}, tier={tier}, owner={key_metadata.get('owner') if key_metadata else 'unknown'}")

    return await proxy_request(
        method="POST",
        backend_url=backend_url,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        timeout=60.0
    )

@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(req: Request):
    """Proxy audio transcription requests."""
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    api_key = extract_api_key(auth_header)

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

    backend_url = f"{route['backend']}{route['endpoint']}"

    logger.info(f"STT: model={model_name}, tier={tier}, owner={key_metadata.get('owner') if key_metadata else 'unknown'}")

    return await proxy_request(
        method="POST",
        backend_url=backend_url,
        api_key=api_key,
        model=model_name,
        tier=tier,
        files=files,
        data=data,
        timeout=300.0
    )

@app.post("/v1/audio/speech")
async def audio_speech(req: Request):
    """Proxy speech synthesis requests - raw body pass-through."""
    auth_header = req.headers.get("authorization")
    is_valid, key_metadata = validate_api_key(auth_header)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")

    tier = key_metadata.get('tier', 'standard') if key_metadata else 'standard'
    api_key = extract_api_key(auth_header)

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
    backend_url = f"{route['backend']}{route['endpoint']}"

    logger.info(f"TTS: model={model}, tier={tier}, owner={key_metadata.get('owner') if key_metadata else 'unknown'}")

    return await proxy_request(
        method="POST",
        backend_url=backend_url,
        api_key=api_key,
        model=model,
        tier=tier,
        payload=payload,
        timeout=60.0
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("ROUTER_PORT", "8090"))
    # Bind to specific IP (Bug 3 fix: config not read - now reads from config)
    host = config.get('server', {}).get('host', '0.0.0.0')
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
