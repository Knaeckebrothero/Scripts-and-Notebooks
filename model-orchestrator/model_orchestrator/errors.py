"""Error envelopes and the mapping from backend error bodies to messages.

Every error the router answers on purpose uses the OpenAI shape
``{"error": {"message", "type"[, "code"]}}`` and carries the header
``X-Router-Error-Source`` (``router`` or ``backend``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from starlette.responses import JSONResponse

SOURCE_HEADER = "X-Router-Error-Source"
RAW_MESSAGE_LIMIT = 1000

# Codes this router sets. Nothing else gets a ``code``.
CODE_MODEL_UNAVAILABLE = "model_unavailable"
CODE_CONCURRENCY = "concurrency_limit_exceeded"
CODE_BACKEND_UNREACHABLE = "backend_unreachable"
CODE_BACKEND_DISCONNECTED = "backend_disconnected"
CODE_ROUTER_BUSY = "router_busy"
CODE_SHUTTING_DOWN = "router_shutting_down"
CODE_BACKEND_TIMEOUT = "backend_timeout"


def error_type(status: int) -> str:
    """The ``type`` field for an HTTP status (same table as the previous router)."""
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_error"
    if status == 429:
        return "rate_limit_error"
    if 500 <= status < 600:
        return "api_error"
    return "invalid_request_error"


def error_payload(status: int, message: str, *, code: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message, "type": error_type(status)}
    if code:
        body["code"] = code
    return {"error": body}


def error_response(
    status: int,
    message: str,
    *,
    code: str | None = None,
    headers: Mapping[str, str] | None = None,
    source: str = "router",
) -> JSONResponse:
    out = dict(headers or {})
    out[SOURCE_HEADER] = source
    return JSONResponse(status_code=status, content=error_payload(status, message, code=code), headers=out)


class RouterError(Exception):
    """An error the router answers with an envelope.

    ``reason`` names the rejection for metrics and logs. ``outcome`` is set only
    for the rejections that are recorded as usage (rate limit, concurrency cap,
    unavailable model).
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str | None = None,
        headers: Mapping[str, str] | None = None,
        reason: str | None = None,
        outcome: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.headers = dict(headers or {})
        self.reason = reason
        self.outcome = outcome

    def response(self) -> JSONResponse:
        return error_response(self.status, self.message, code=self.code, headers=self.headers)


def extract_backend_message(body: bytes, status: int) -> str:
    """The backend's own error message, taken from its response body.

    Understands the shapes vLLM, TEI and FastAPI-based servers produce:
    ``{"error": {"message": ...}}``, a top-level ``message``, an ``error``
    string and ``detail``. Anything else falls back to the raw text, truncated.
    """
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return f"Backend returned HTTP {status} with an empty body"
    try:
        parsed = json.loads(text)
    except ValueError:
        return _truncate(text)
    if isinstance(parsed, dict):
        found = _message_from_object(parsed)
        if found:
            return found
    return _truncate(text)


def extract_backend_code(body: bytes) -> str | None:
    """The ``error.code`` of an error envelope, when it is a string (another router's)."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else None


def _message_from_object(obj: dict[str, Any]) -> str | None:
    error = obj.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    message = obj.get("message")
    if isinstance(message, str) and message.strip():
        return message
    if isinstance(error, str) and error.strip():
        return error
    return _message_from_detail(obj.get("detail"))


def _message_from_detail(detail: Any) -> str | None:
    if isinstance(detail, str):
        return detail if detail.strip() else None
    if isinstance(detail, dict):
        for key in ("message", "msg", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return _truncate(json.dumps(detail, ensure_ascii=False))
    if isinstance(detail, list) and detail:
        parts: list[str] = []
        for item in detail:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("msg"), str):
                loc = item.get("loc")
                where = ".".join(str(p) for p in loc) if isinstance(loc, list) and loc else ""
                parts.append(f"{where}: {item['msg']}" if where else item["msg"])
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return _truncate("; ".join(parts))
    return None


def _truncate(text: str) -> str:
    if len(text) <= RAW_MESSAGE_LIMIT:
        return text
    return text[:RAW_MESSAGE_LIMIT] + "... (truncated)"
