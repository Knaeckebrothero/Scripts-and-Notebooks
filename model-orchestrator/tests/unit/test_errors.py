"""Error envelopes and backend message extraction (C4, C19)."""

from __future__ import annotations

import json

import pytest

from model_orchestrator.errors import (
    RAW_MESSAGE_LIMIT,
    SOURCE_HEADER,
    RouterError,
    error_payload,
    error_response,
    error_type,
    extract_backend_code,
    extract_backend_message,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "invalid_request_error"),
        (401, "authentication_error"),
        (403, "permission_error"),
        (404, "invalid_request_error"),
        (413, "invalid_request_error"),
        (422, "invalid_request_error"),
        (429, "rate_limit_error"),
        (500, "api_error"),
        (503, "api_error"),
        (504, "api_error"),
    ],
)
def test_error_type(status: int, expected: str) -> None:
    assert error_type(status) == expected


def test_envelope_has_no_code_unless_one_is_named() -> None:
    assert error_payload(404, "Model 'x' not found") == {
        "error": {"message": "Model 'x' not found", "type": "invalid_request_error"}
    }
    assert error_payload(503, "down", code="model_unavailable") == {
        "error": {"message": "down", "type": "api_error", "code": "model_unavailable"}
    }


def test_response_carries_the_source_header() -> None:
    router = error_response(429, "Rate limit exceeded", headers={"Retry-After": "1"})
    assert router.status_code == 429
    assert router.headers[SOURCE_HEADER] == "router"
    assert router.headers["Retry-After"] == "1"
    assert json.loads(bytes(router.body)) == {
        "error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}
    }
    backend = error_response(400, "bad prompt", source="backend")
    assert backend.headers[SOURCE_HEADER] == "backend"


def test_router_error_builds_its_response() -> None:
    error = RouterError(
        503, "gone", code="model_unavailable", headers={"Retry-After": "30"}, reason="unavailable"
    )
    response = error.response()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.headers[SOURCE_HEADER] == "router"
    assert json.loads(bytes(response.body))["error"]["code"] == "model_unavailable"


# -- the backend's own message (C4) ----------------------------------------------------


def body(obj: object) -> bytes:
    return json.dumps(obj).encode()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        # vLLM, current: an OpenAI-style envelope.
        (
            body(
                {
                    "error": {
                        "message": "max_tokens is too large",
                        "type": "BadRequestError",
                        "param": None,
                        "code": 400,
                    }
                }
            ),
            "max_tokens is too large",
        ),
        # vLLM, older: the error object at the top level.
        (
            body(
                {
                    "object": "error",
                    "message": "The model `x` does not exist.",
                    "type": "NotFoundError",
                    "code": 404,
                }
            ),
            "The model `x` does not exist.",
        ),
        # TEI: an error string plus a type.
        (
            body({"error": "Input validation error: inputs cannot be empty", "error_type": "Validation"}),
            "Input validation error: inputs cannot be empty",
        ),
        # FastAPI-style servers.
        (body({"detail": "Voice not found"}), "Voice not found"),
        (
            body({"detail": {"error": "validation_error", "message": "Unsupported format"}}),
            "Unsupported format",
        ),
        (body({"detail": {"error": "only an error key"}}), "only an error key"),
        (
            body({"detail": [{"loc": ["body", "input"], "msg": "Field required", "type": "missing"}]}),
            "body.input: Field required",
        ),
        (body({"detail": ["first", "second"]}), "first; second"),
        # Precedence: error.message, then message, then an error string, then detail.
        (body({"error": {"message": "inner"}, "message": "outer", "detail": "d"}), "inner"),
        (body({"error": "string", "message": "outer", "detail": "d"}), "outer"),
        (body({"error": {"no_message": 1}, "detail": "d"}), "d"),
    ],
)
def test_message_is_extracted(raw: bytes, message: str) -> None:
    assert extract_backend_message(raw, 400) == message


@pytest.mark.parametrize(
    "raw",
    [
        b"Internal Server Error",
        b"<html><body>502 Bad Gateway</body></html>",
        b'"just a string"',
        b"[1, 2]",
        b'{"unrelated": true}',
    ],
)
def test_anything_else_falls_back_to_the_raw_text(raw: bytes) -> None:
    assert extract_backend_message(raw, 500) == raw.decode()


def test_raw_fallback_is_truncated() -> None:
    message = extract_backend_message(b"x" * 5000, 500)
    assert message.startswith("x" * RAW_MESSAGE_LIMIT)
    assert message.endswith("(truncated)")
    assert len(message) < RAW_MESSAGE_LIMIT + 50


def test_an_extracted_message_is_not_truncated() -> None:
    long = "context length exceeded: " + "tok " * 1000
    assert extract_backend_message(body({"error": {"message": long}}), 400) == long


@pytest.mark.parametrize("raw", [b"", b"   \n"])
def test_empty_body_still_gives_a_message(raw: bytes) -> None:
    assert extract_backend_message(raw, 502) == "Backend returned HTTP 502 with an empty body"


def test_undecodable_bytes_do_not_raise() -> None:
    assert extract_backend_message(b"\xff\xfe broken", 500)


def test_backend_code_is_read_only_when_it_is_a_string() -> None:
    assert (
        extract_backend_code(body({"error": {"message": "m", "code": "model_unavailable"}}))
        == "model_unavailable"
    )
    assert extract_backend_code(body({"error": {"message": "m", "code": 400}})) is None
    assert extract_backend_code(body({"error": "text"})) is None
    assert extract_backend_code(b"not json") is None
    assert extract_backend_code(b"[]") is None
