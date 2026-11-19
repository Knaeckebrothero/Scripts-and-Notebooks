"""The request log line, in text and JSON (design 7.7)."""

from __future__ import annotations

import json
import logging

from model_orchestrator.auth import KeyRecord
from model_orchestrator.context import RequestRecord
from model_orchestrator.logs import _FIELDS_ATTR, JsonFormatter, TextFormatter, request_id_var


def record_with(fields: dict[str, object] | None, message: str = "request") -> logging.LogRecord:
    record = logging.LogRecord("model_orchestrator.request", logging.INFO, __file__, 1, message, None, None)
    if fields is not None:
        setattr(record, _FIELDS_ATTR, fields)
    return record


def sample() -> RequestRecord:
    record = RequestRecord(request_id="rid-1", method="POST", path="/v1/chat/completions")
    record.endpoint = "/v1/chat/completions"
    record.key = KeyRecord(7, "ci key", "alice", "sk-abcd", 100, None, None, True, None)
    record.model, record.route, record.backend = "gpt-4o-mini", "Gemma-Pool", "http://backend-a.invalid:8000"
    record.status, record.outcome, record.upstream_status = 200, "ok", 200
    record.stream, record.attempts, record.forwarded = True, 2, True
    record.first_byte_at = record.started + 0.25
    record.prompt_tokens, record.completion_tokens = 11, 7
    return record


def test_log_fields_cover_what_the_design_lists() -> None:
    fields = sample().log_fields()
    for name in (
        "key_id",
        "key",
        "owner",
        "model",
        "route",
        "backend",
        "endpoint",
        "status",
        "outcome",
        "upstream_status",
        "stream",
        "attempts",
        "duration_ms",
        "ttfb_ms",
        "prompt_tokens",
        "completion_tokens",
    ):
        assert name in fields
    assert fields["key_id"] == 7 and fields["key"] == "ci key" and fields["owner"] == "alice"
    assert fields["ttfb_ms"] == 250 and fields["stream"] is True and fields["attempts"] == 2
    assert isinstance(fields["duration_ms"], int)


def test_a_rejected_request_logs_its_reason_as_outcome() -> None:
    record = RequestRecord(request_id="r", method="POST", path="/v1/chat/completions")
    record.status, record.reason = 401, "auth"
    fields = record.log_fields()
    assert fields["outcome"] == "auth" and fields["key_id"] is None and fields["model"] is None


def test_text_format() -> None:
    token = request_id_var.set("rid-1")
    try:
        line = TextFormatter().format(record_with(sample().log_fields()))
    finally:
        request_id_var.reset(token)
    assert " [rid-1] INFO model_orchestrator.request: request " in line
    assert "status=200" in line and "outcome=ok" in line and "stream=true" in line
    assert 'key="ci key"' in line, "values with spaces are quoted"
    assert "prompt_tokens=11 completion_tokens=7" in line
    assert "error=" not in line, "empty fields are left out"
    assert "\n" not in line


def test_text_format_without_fields_and_outside_a_request() -> None:
    line = TextFormatter().format(record_with(None, "config reloaded"))
    assert line.endswith("[-] INFO model_orchestrator.request: config reloaded")


def test_json_format_is_one_object_per_line() -> None:
    token = request_id_var.set("rid-1")
    try:
        line = JsonFormatter().format(record_with(sample().log_fields()))
    finally:
        request_id_var.reset(token)
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["request_id"] == "rid-1" and entry["level"] == "INFO" and entry["msg"] == "request"
    assert entry["key_id"] == 7 and entry["route"] == "Gemma-Pool" and entry["stream"] is True
    assert entry["error"] is None, "JSON keeps every field, so the schema is stable"
    assert entry["ts"].endswith("+00:00")


def test_no_prompt_or_token_material_in_the_log_line() -> None:
    fields = sample().log_fields()
    assert "sk-abcd" not in json.dumps(fields), "not even the key prefix"
    assert not {"messages", "prompt", "content", "authorization"} & set(fields)


async def test_cleanups_all_run_even_when_one_fails_or_is_cancelled() -> None:
    import asyncio

    import pytest

    record = RequestRecord(request_id="r", method="GET", path="/x")
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("cleanup bug")

    async def cancelled() -> None:
        raise asyncio.CancelledError

    record.add_cleanup(lambda: ran.append("first registered, last run"))
    record.add_cleanup(cancelled)
    record.add_cleanup(boom)
    record.add_cleanup(lambda: ran.append("last registered, first run"))
    with pytest.raises(asyncio.CancelledError):
        await record.run_cleanups()
    assert ran == ["last registered, first run", "first registered, last run"]
    await record.run_cleanups()  # nothing left: a second call is harmless
    assert len(ran) == 2
