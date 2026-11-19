"""The public HTTP surface: an app factory and thin handlers.

The inference endpoints are driven by one table (``ENDPOINTS``). Adding a
pass-through endpoint is one entry there plus its tests.

Order of checks for a request (design 7.4): auth, body, model lookup (404),
type check (400), allowlist (403), per-minute limit, concurrency cap,
availability (503), forward. A request rejected before the limiter does not
consume the per-minute budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

from fastapi import FastAPI
from starlette.datastructures import FormData, Headers, MutableHeaders, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import usage
from .auth import AUTH_ERROR_MESSAGE, KeyRecord
from .clients import TransportFactory
from .config import RouterConfig
from .context import SCOPE_KEY, RequestRecord, get_record
from .db import Database
from .errors import (
    CODE_CONCURRENCY,
    CODE_MODEL_UNAVAILABLE,
    CODE_SHUTTING_DOWN,
    RouterError,
    error_response,
)
from .logs import request_id_var
from .payload import apply_request_defaults, drop_null_top_n, encode_json, inject_stream_usage
from .proxy import (
    CLIENT_CLOSED_STATUS,
    MSG_SHUTTING_DOWN,
    SHUTDOWN_WRITE_TIMEOUT_S,
    FormPart,
    ForwardPlan,
    UpstreamAborted,
    absorb_cancellation,
)
from .registry import ModelEntry, model_object
from .settings import Settings
from .state import VOICES_PATH, RouterState

logger = logging.getLogger(__name__)

RETRY_AFTER_UNAVAILABLE = "30"
RETRY_AFTER_CONCURRENCY = "1"


@dataclass(frozen=True)
class EndpointSpec:
    path: str
    route_types: frozenset[str]
    body: str  # "json" or "multipart"
    # Completes "Model 'x' is not ..." for a model of the wrong type.
    kind: str
    # Default read timeout in seconds; None means CHAT_READ_TIMEOUT_S.
    read_timeout_s: float | None
    stream: bool = False
    count_tokens: bool = True
    adjust: Callable[[dict[str, Any]], None] | None = None

    def timeout(self, settings: Settings, entry: ModelEntry) -> float:
        if entry.target.timeout_s is not None:
            return entry.target.timeout_s
        return settings.chat_read_timeout_s if self.read_timeout_s is None else self.read_timeout_s


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec(
        "/v1/chat/completions", frozenset({"chat", "vision"}), "json", "a chat model", None, stream=True
    ),
    EndpointSpec("/v1/embeddings", frozenset({"embedding"}), "json", "an embedding model", 60.0),
    EndpointSpec(
        "/v1/rerank", frozenset({"reranker"}), "json", "a reranker model", 60.0, adjust=drop_null_top_n
    ),
    EndpointSpec(
        "/v1/audio/transcriptions",
        frozenset({"stt"}),
        "multipart",
        "a speech-to-text model",
        300.0,
        count_tokens=False,
    ),
    EndpointSpec(
        "/v1/audio/speech", frozenset({"tts"}), "json", "a text-to-speech model", 60.0, count_tokens=False
    ),
)


def _state(request: Request) -> RouterState:
    state = getattr(request.app.state, "router", None)
    if not isinstance(state, RouterState):
        raise RuntimeError("router state missing: the application lifespan has not run")
    return state


# -- request context -------------------------------------------------------------


class RequestContextMiddleware:
    """Request id, the per-request record, and the last line of defence for errors.

    Plain ASGI on purpose: it must see the response through to its last byte
    (a stream ends long after the handler returned) and must not get between a
    handler and the client's disconnect.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = Headers(scope=scope).get("x-request-id") or uuid.uuid4().hex[:12]
        record = RequestRecord(request_id=request_id, method=scope["method"], path=scope["path"])
        scope[SCOPE_KEY] = record
        token = request_id_var.set(request_id)
        started = False

        async def send_with_context(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                record.status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                if record.rate_headers:
                    for name, value in record.rate_headers.items():
                        headers[name] = value
            elif message["type"] == "http.response.body" and record.first_byte_at is None:
                if message.get("body"):
                    record.first_byte_at = time.perf_counter()
            await send(message)

        state = getattr(getattr(scope.get("app"), "state", None), "router", None)
        if isinstance(state, RouterState):
            state.request_started()
        try:
            await self.app(scope, receive, send_with_context)
        except ClientDisconnect:
            # The client left while its request body was being read. Nobody to answer.
            record.status = CLIENT_CLOSED_STATUS
            record.reason = record.reason or "client_closed"
        except asyncio.CancelledError:
            # Only the server cancels a request: at shutdown, once its grace period is
            # over. The request is then ended properly instead of just vanishing.
            if not isinstance(state, RouterState) or not await state.wait_shutting_down():
                raise
            absorb_cancellation()
            record.reason = "shutting_down"
            if not started:
                record.outcome = usage.UNAVAILABLE
                response = error_response(503, MSG_SHUTTING_DOWN, code=CODE_SHUTTING_DOWN)
                with contextlib.suppress(OSError, TimeoutError):
                    async with asyncio.timeout(SHUTDOWN_WRITE_TIMEOUT_S):
                        await response(scope, receive, send_with_context)
        except UpstreamAborted as exc:
            # Headers are out and the body cannot be completed: drop the connection.
            logger.warning("upstream body broke off mid-response (%s); closing the client connection", exc)
            raise
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception("unhandled error")
            if isinstance(state, RouterState):
                state.metrics.internal_errors.inc()
            if started:
                raise
            record.reason = "internal_error"
            response = error_response(500, "Internal server error")
            await response(scope, receive, send_with_context)
        finally:
            try:
                await record.run_cleanups()
            finally:
                # Whatever happened, the request is recorded and counted as over.
                if isinstance(state, RouterState):
                    state.finish(record)
                    state.request_finished()
                request_id_var.reset(token)


async def _router_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RouterError)
    record = get_record(request)
    record.reason = exc.reason
    if exc.outcome is not None:
        record.outcome = exc.outcome
    record.error = exc.message[:300]
    return exc.response()


async def _http_error_handler(request: Request, exc: Exception) -> Response:
    """Unknown paths and wrong methods, in the same envelope as everything else."""
    assert isinstance(exc, StarletteHTTPException)
    record = get_record(request)
    record.reason = "not_found" if exc.status_code in (404, 405) else "invalid_request"
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Request failed"
    return error_response(exc.status_code, message, headers=exc.headers)


# -- reading request bodies ---------------------------------------------------------


def _too_large(limit: int) -> RouterError:
    megabytes = limit / (1024 * 1024)
    return RouterError(
        413, f"Request body is larger than the limit of {megabytes:g} MB", reason="body_too_large"
    )


def _check_declared_length(request: Request, limit: int) -> None:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)


async def read_body(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it exceeds the limit."""
    _check_declared_length(request, limit)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


def parse_json_object(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        raise RouterError(400, "Invalid JSON body", reason="invalid_request") from None
    if not isinstance(payload, dict):
        raise RouterError(400, "Request body must be a JSON object", reason="invalid_request")
    return payload


async def read_form(request: Request, limit: int) -> FormData:
    """Parse a multipart body, bounded by the same size limit."""
    _check_declared_length(request, limit)
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise RouterError(400, "Expected a multipart/form-data body", reason="invalid_request")
    received = 0
    upstream_receive = request.receive

    async def limited_receive() -> Message:
        nonlocal received
        message = await upstream_receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _too_large(limit)
        return message

    try:
        return await Request(request.scope, limited_receive).form()
    except StarletteHTTPException as exc:
        raise RouterError(400, f"Invalid multipart body: {exc.detail}", reason="invalid_request") from None


# -- the inference endpoints ----------------------------------------------------------


def _authenticate(state: RouterState, request: Request, record: RequestRecord) -> KeyRecord:
    key = state.keys.authenticate(request.headers.get("authorization"))
    if key is None:
        raise RouterError(401, AUTH_ERROR_MESSAGE, reason="auth")
    record.key = key
    return key


def _unavailable(model: str, *, counted: bool) -> RouterError:
    """503 for a known model with no backend up. ``counted``: record it as usage (needs a key)."""
    return RouterError(
        503,
        f"Model '{model}' is temporarily unavailable",
        code=CODE_MODEL_UNAVAILABLE,
        headers={"Retry-After": RETRY_AFTER_UNAVAILABLE},
        reason="unavailable",
        outcome=usage.UNAVAILABLE if counted else None,
    )


def _admit(
    state: RouterState, record: RequestRecord, key: KeyRecord, spec: EndpointSpec, model: Any
) -> ModelEntry:
    """Model lookup, type check, allowlist, limits and availability, in that order."""
    if model is not None and not isinstance(model, str):
        # A number, a list, an object: a malformed request, not an unknown model.
        raise RouterError(400, "'model' must be a string", reason="invalid_request")
    entry = state.registry.table.get(model)
    if entry is None:
        raise RouterError(404, f"Model '{model}' not found", reason="unknown_model")
    record.model = entry.model_id
    record.route = entry.route_name
    if entry.route_type is not None and entry.route_type not in spec.route_types:
        raise RouterError(400, f"Model '{model}' is not {spec.kind}", reason="wrong_endpoint")
    if not key.allows(entry.route_name):
        raise RouterError(
            403, f"API key is not allowed to use model route '{entry.route_name}'", reason="forbidden"
        )
    decision = state.rate_limiter.check(key.id, key.rate_limit_rpm)
    record.rate_headers = decision.headers()
    if not decision.allowed:
        raise RouterError(429, "Rate limit exceeded", reason="rate", outcome=usage.RATE_LIMITED)
    # A key without a cap of its own gets the default one, if the operator set one.
    cap = key.max_concurrency
    if cap is None and state.settings.default_max_concurrency > 0:
        cap = state.settings.default_max_concurrency
    if not state.concurrency.try_acquire(key.id, cap):
        raise RouterError(
            429,
            f"Too many requests in flight for this API key (limit {cap})",
            code=CODE_CONCURRENCY,
            headers={"Retry-After": RETRY_AFTER_CONCURRENCY},
            reason="concurrency",
            outcome=usage.CONCURRENCY_LIMITED,
        )
    # Held until the response has been sent or aborted; for a stream, until it ends.
    record.add_cleanup(partial(state.concurrency.release, key.id))
    if not state.is_available(entry):
        raise _unavailable(entry.model_id, counted=True)
    return entry


async def _handle_json(request: Request, spec: EndpointSpec) -> Response:
    state = _state(request)
    record = get_record(request)
    record.endpoint = spec.path
    key = _authenticate(state, request, record)
    payload = parse_json_object(await read_body(request, state.settings.max_body_bytes))
    # Whether to stream is the client's own choice, read before any default is merged in.
    stream = spec.stream and bool(payload.get("stream", False))
    entry = _admit(state, record, key, spec, payload.get("model"))
    target = entry.target

    if entry.rewrite_to is not None:
        payload["model"] = entry.rewrite_to
    if target.request_defaults:
        apply_request_defaults(payload, target.request_defaults)
    if spec.adjust is not None:
        spec.adjust(payload)
    strip_usage_event = False
    if stream and spec.count_tokens and target.usage_from_stream:
        strip_usage_event = inject_stream_usage(payload)

    plan = ForwardPlan(
        entry=entry,
        method="POST",
        path=spec.path if target.is_upstream else (target.endpoint or spec.path),
        read_timeout_s=spec.timeout(state.settings, entry),
        json_body=encode_json(payload),
        stream=stream,
        strip_usage_event=strip_usage_event,
        count_tokens=spec.count_tokens,
    )
    return await state.forwarder.forward(plan, record, request.receive)


async def _handle_multipart(request: Request, spec: EndpointSpec) -> Response:
    state = _state(request)
    record = get_record(request)
    record.endpoint = spec.path
    key = _authenticate(state, request, record)
    form = await read_form(request, state.settings.max_body_bytes)
    record.add_cleanup(form.close)

    if not isinstance(form.get("file"), UploadFile):
        raise RouterError(400, "Missing 'file': an uploaded file is required", reason="invalid_request")
    model: Any = form.get("model")
    if model is None:
        # No model named: the first speech-to-text route.
        default = state.registry.table.first_of_type("stt")
        if default is None:
            raise RouterError(404, "No speech-to-text model is configured", reason="unknown_model")
        model = default.model_id
    elif not isinstance(model, str):
        raise RouterError(400, "'model' must be a text field", reason="invalid_request")
    entry = _admit(state, record, key, spec, model)
    target = entry.target
    backend_model = entry.rewrite_to if entry.rewrite_to is not None else entry.model_id

    # Every field is forwarded, in order, repeated ones included. Only `model` is rewritten.
    parts: list[FormPart] = []
    model_sent = False
    for name, value in form.multi_items():
        if isinstance(value, UploadFile):
            content = await value.read()
            if name == "file":
                filename, content_type = value.filename or "audio.wav", value.content_type or "audio/wav"
            else:
                filename = value.filename or "upload"
                content_type = value.content_type or "application/octet-stream"
            parts.append((name, (filename, content, content_type)))
        elif name == "model":
            parts.append((name, (None, backend_model.encode("utf-8"))))
            model_sent = True
        else:
            parts.append((name, (None, value.encode("utf-8"))))
    if not model_sent:
        parts.append(("model", (None, backend_model.encode("utf-8"))))

    plan = ForwardPlan(
        entry=entry,
        method="POST",
        path=spec.path if target.is_upstream else (target.endpoint or spec.path),
        read_timeout_s=spec.timeout(state.settings, entry),
        form_parts=parts,
        count_tokens=spec.count_tokens,
    )
    return await state.forwarder.forward(plan, record, request.receive)


def _endpoint_handler(spec: EndpointSpec) -> Callable[[Request], Awaitable[Response]]:
    handle = _handle_multipart if spec.body == "multipart" else _handle_json

    async def endpoint(request: Request) -> Response:
        return await handle(request, spec)

    endpoint.__name__ = "proxy_" + spec.path.strip("/").replace("/", "_")
    endpoint.__doc__ = (
        f"Forward to the backend of the requested model ({', '.join(sorted(spec.route_types))} routes). "
        "Needs `Authorization: Bearer <key>`."
    )
    return endpoint


# -- open endpoints ---------------------------------------------------------------------


async def livez(request: Request) -> Response:
    """Liveness: 200 as long as the process runs. Cheap on purpose."""
    get_record(request).quiet = True
    return JSONResponse({"status": "ok"})


async def health(request: Request) -> Response:
    """Overall status from cached state. Always 200; no probe is made here."""
    get_record(request).quiet = True
    return JSONResponse(_state(request).public_health())


async def list_models(request: Request) -> Response:
    """The model ids that can be used right now (no auth)."""
    state = _state(request)
    get_record(request).quiet = True
    entries = state.registry.table.entries.values()
    data = [model_object(entry) for entry in entries if state.is_available(entry)]
    return JSONResponse({"object": "list", "data": data})


async def get_model(request: Request) -> Response:
    """One model id (no auth). Ids containing '/' work too."""
    state = _state(request)
    record = get_record(request)
    record.quiet = True
    model_id = request.path_params["model_id"]
    if model_id == "":
        # "/v1/models/": redirected to the listing, as it always was.
        return RedirectResponse(url=str(request.url.replace(path="/v1/models")), status_code=307)
    entry = state.registry.table.get(model_id)
    if entry is None:
        raise RouterError(404, "Model not found", reason="unknown_model")
    if not state.is_available(entry):
        raise _unavailable(entry.model_id, counted=False)
    return JSONResponse(model_object(entry))


async def audio_voices(request: Request) -> Response:
    """The voices of a text-to-speech backend (no auth), cached for five minutes per route.

    Open to anyone, so it must not turn callers into backend load: an answer is
    cached, a failure is remembered for a few seconds, and callers that arrive
    while the backend is being asked wait for that one fetch.
    """
    state = _state(request)
    record = get_record(request)
    record.endpoint = VOICES_PATH
    table = state.registry.table
    model = request.query_params.get("model")
    if model is not None:
        entry = table.get(model)
        if entry is None:
            raise RouterError(404, f"Model '{model}' not found", reason="unknown_model")
        if entry.route_type is not None and entry.route_type != "tts":
            raise RouterError(400, f"Model '{model}' is not a text-to-speech model", reason="wrong_endpoint")
    else:
        entry = table.first_of_type("tts")
        if entry is None:
            raise RouterError(404, "No text-to-speech backend is configured", reason="unknown_model")
    target = entry.target
    record.model = entry.model_id
    record.route = entry.route_name

    # An upstream router picks the backend by model id, so its answers are cached per id.
    cache_key = (target.kind, target.name, entry.model_id if target.is_upstream else "")
    result = state.voices.get(cache_key)
    if result is None:
        if not state.is_available(entry):
            raise _unavailable(entry.model_id, counted=False)
        result = await state.fetch_voices(entry, cache_key)
    record.upstream_status = result.upstream_status
    record.outcome = result.outcome
    return Response(content=result.body, status_code=result.status, headers=dict(result.headers))


# -- the app ------------------------------------------------------------------------------


def create_app(
    settings: Settings,
    config: RouterConfig,
    *,
    listen_host: str = "0.0.0.0",
    database: Database | None = None,
    transport_factory: TransportFactory | None = None,
) -> FastAPI:
    """Build the application. All runtime state is created in its lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = RouterState(
            settings, config, database=database, transport_factory=transport_factory, listen_host=listen_host
        )
        await state.start()
        app.state.router = state
        try:
            yield
        finally:
            await state.stop()

    docs = settings.expose_docs
    app = FastAPI(
        title="Model Orchestrator",
        description="OpenAI API-compatible gateway for self-hosted models",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(RouterError, _router_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)

    app.add_api_route("/livez", livez, methods=["GET"], response_model=None)
    app.add_api_route("/health", health, methods=["GET"], response_model=None)
    app.add_api_route("/v1/models", list_models, methods=["GET"], response_model=None)
    app.add_api_route("/v1/models/{model_id:path}", get_model, methods=["GET"], response_model=None)
    app.add_api_route(VOICES_PATH, audio_voices, methods=["GET"], response_model=None)
    for spec in ENDPOINTS:
        app.add_api_route(spec.path, _endpoint_handler(spec), methods=["POST"], response_model=None)
    return app
