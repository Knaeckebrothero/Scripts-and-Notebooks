"""Logging setup: text or JSON lines, with the request id on every line.

Nothing is configured at import time; ``configure_logging`` is called once when
the process starts. Prompts and responses are never logged at INFO.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str] = ContextVar("model_orchestrator_request_id", default="-")

REQUEST_LOGGER = "model_orchestrator.request"
_FIELDS_ATTR = "router_fields"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def _text_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text == "" or any(ch.isspace() or ch in '"=' for ch in text):
        return json.dumps(text, ensure_ascii=False)
    return text


class TextFormatter(logging.Formatter):
    """``time [request id] LEVEL logger: message key=value ...``"""

    def __init__(self) -> None:
        super().__init__("%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        base = super().format(record)
        fields = getattr(record, _FIELDS_ATTR, None)
        if not fields:
            return base
        extras = " ".join(
            f"{name}={_text_value(value)}" for name, value in fields.items() if value is not None
        )
        return f"{base} {extras}" if extras else base


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "msg": record.getMessage(),
        }
        fields = getattr(record, _FIELDS_ATTR, None)
        if fields:
            out.update(fields)
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_logging(level: str, log_format: str) -> None:
    """Send every log line (ours, uvicorn's, the pool's) through one handler on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    numeric = logging.getLevelNamesMapping().get(level.upper())
    root.setLevel(numeric if numeric is not None else logging.INFO)
    # These log every outbound request at INFO, with the backend URL.
    for noisy in ("httpx", "httpcore", "hpack", "h2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if numeric is None:
        logging.getLogger(__name__).warning("unknown ROUTER_LOG_LEVEL %r, using INFO", level)


def log_request(fields: dict[str, Any], *, level: int = logging.INFO) -> None:
    """The one line per request."""
    logging.getLogger(REQUEST_LOGGER).log(level, "request", extra={_FIELDS_ATTR: fields})
