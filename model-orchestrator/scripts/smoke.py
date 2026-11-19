# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Live smoke test for a deployed model router. Run it by hand after a deploy.

    export SMOKE_API_KEY=sk-mo-...
    uv run python scripts/smoke.py --base-url https://router.example.org

It asks the router what it offers (``/health``, ``/v1/models``) and makes one
cheap call per endpoint type: chat without and with streaming (for chat and
for vision models), embeddings, rerank, the voice list, text-to-speech, and
speech-to-text on the audio that text-to-speech returned. It prints one line
per check and exits 0 when all passed, 1 when one failed, 2 when it could not
start (no URL, no key, unreadable CA file).

Standard library plus httpx. The file runs on its own, so it can be copied to
any machine: ``uv run smoke.py ...`` installs httpx for that one run.

Certificates that clients cannot verify: ``--ca-file bundle.pem`` trusts a
private CA, ``--insecure`` switches verification off.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import ssl
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

OK, FAIL, SKIP = "ok", "FAIL", "skip"
ROUTE_TYPES = ("chat", "vision", "embedding", "reranker", "tts", "stt")
PROMPT = "Reply with the single word: pong"
SPEECH_TEXT = "This is a smoke test."


class CheckFailed(Exception):
    """A check did not pass. The message goes into the table."""


class Skipped(Exception):
    """A check does not apply to this router or backend."""


@dataclass
class Row:
    check: str
    model: str
    result: str
    seconds: float
    detail: str


# ---- helpers -----------------------------------------------------------------


def error_text(response: httpx.Response) -> str:
    """One line for a response that is not a success: status, code, message, request id."""
    message, code = response.text.strip()[:200], None
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            message, code = str(error.get("message", message))[:200], error.get("code")
        elif isinstance(error, str):
            message = error[:200]
    except (ValueError, AttributeError):
        pass
    parts = [f"HTTP {response.status_code}"]
    if code:
        parts.append(str(code))
    text = " ".join(parts) + (f": {message}" if message else "")
    source = response.headers.get("x-router-error-source")
    request_id = response.headers.get("x-request-id")
    extras = [f"from the {source}" if source else "", f"request id {request_id}" if request_id else ""]
    extras = [e for e in extras if e]
    return f"{text} ({', '.join(extras)})" if extras else text


def expect_ok(response: httpx.Response) -> httpx.Response:
    if response.status_code != 200:
        raise CheckFailed(error_text(response))
    return response


def json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise CheckFailed(f"HTTP {response.status_code}, but the body is not JSON") from None
    if not isinstance(body, dict):
        raise CheckFailed("the body is JSON, but not an object")
    return body


def transport_text(exc: httpx.HTTPError) -> str:
    text = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
    if "CERTIFICATE_VERIFY_FAILED" in text:
        text += " (trust the CA with --ca-file, or use --insecure)"
    return text[:300]


def has_reasoning(obj: Any) -> bool:
    """vLLM calls the field ``reasoning``; some servers call it ``reasoning_content``."""
    return isinstance(obj, dict) and bool(obj.get("reasoning") or obj.get("reasoning_content"))


def silence_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def voice_names(body: Any) -> list[str]:
    """The names in a voice list: ``{"voices": [...]}`` or a bare list, of strings or objects."""
    items = body.get("voices") if isinstance(body, dict) else body
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("voice_id")
            if isinstance(name, str):
                names.append(name)
    return names


def choose_models(
    listed: list[dict[str, Any]], wanted: dict[str, list[str]], all_routes: bool
) -> dict[str, list[str]]:
    """Which model ids to call, per route type.

    One id per type, or with ``all_routes`` one id per route. ``--model`` wins.
    The type and route of an id come from the router's ``x_router`` object.
    """
    chosen: dict[str, list[str]] = {}
    seen_routes: set[tuple[str, str]] = set()
    for entry in listed:
        info = entry.get("x_router")
        model_id = entry.get("id")
        if not isinstance(info, dict) or not isinstance(model_id, str):
            continue
        route_type, route = info.get("type"), info.get("route")
        if route_type not in ROUTE_TYPES or (route_type, route) in seen_routes:
            continue
        if route_type in chosen and not all_routes:
            continue
        seen_routes.add((route_type, route))
        chosen.setdefault(route_type, []).append(model_id)
    chosen.update(wanted)
    return chosen


# ---- the checks --------------------------------------------------------------


class Smoke:
    def __init__(self, client: httpx.Client, args: argparse.Namespace) -> None:
        self.client = client
        self.args = args
        self.rows: list[Row] = []
        self.reachable = False  # /health answered, whatever it said
        self.voice: str | None = args.voice
        self.audio: tuple[bytes, str] | None = None  # what text-to-speech returned, and its type

    def step(self, check: str, model: str, action: Callable[[], str]) -> bool:
        """Run one check and add its row. Returns False when it failed."""
        started = time.perf_counter()
        try:
            result, detail = OK, action()
        except Skipped as exc:
            result, detail = SKIP, str(exc)
        except CheckFailed as exc:
            result, detail = FAIL, str(exc)
        except httpx.HTTPError as exc:
            result, detail = FAIL, transport_text(exc)
        self.rows.append(Row(check, model, result, round(time.perf_counter() - started, 3), detail))
        return result != FAIL

    def run(self) -> list[Row]:
        models: list[dict[str, Any]] = []
        self.step("health", "-", self.health)
        if not self.reachable:
            return self.rows  # no point in ten more rows saying the same thing
        self.step("auth", "-", self.auth)
        self.step("models", "-", lambda: self.models(models))
        chosen = choose_models(models, self.args.model, self.args.all_routes)
        if not chosen:
            if self.rows[-1].result == OK:
                self.rows[-1].result = FAIL
                self.rows[-1].detail += (
                    "; no entry carries x_router (a router older than 3.0?), "
                    "so name the models to call: --model chat=ID --model embedding=ID ..."
                )
            return self.rows

        for route_type in ("chat", "vision"):
            for model in chosen.get(route_type, []):
                label = "chat" if route_type == "chat" else "vision chat"
                self.step(label, model, lambda m=model: self.chat(m))
                self.step(f"{label}, stream", model, lambda m=model: self.chat_stream(m))
        for model in chosen.get("embedding", []):
            self.step("embeddings", model, lambda m=model: self.embeddings(m))
        for model in chosen.get("reranker", []):
            self.step("rerank", model, lambda m=model: self.rerank(m))
        for model in chosen.get("tts", []):
            self.step("voices", model, lambda m=model: self.voices(m))
            self.step("speech", model, lambda m=model: self.speech(m))
        for model in chosen.get("stt", []):
            self.step("transcription", model, lambda m=model: self.transcription(m))
        for route_type in ROUTE_TYPES:
            if route_type not in chosen:
                self.rows.append(Row(route_type, "-", SKIP, 0.0, f"the router lists no {route_type} model"))
        return self.rows

    def health(self) -> str:
        body = json_body(expect_ok(self.client.get("/health")))
        self.reachable = True
        status, version = body.get("status"), body.get("version")
        routes = body.get("routes")
        text = f"status {status}" + (f", version {version}" if version else "")
        if not isinstance(routes, dict):
            # The 2.x router reports backends in another shape; nothing to count here.
            if status != "healthy":
                raise CheckFailed(text)
            return text + ", no per-route status in this answer"
        down = sorted(name for name, state in routes.items() if state != "up")
        text += f", {len(routes) - len(down)} of {len(routes)} route(s) up"
        if down:
            text += f", {len(down)} route(s) down: {', '.join(down)}"
        if status != "healthy" or (down and not self.args.allow_down):
            raise CheckFailed(text)
        return text

    def auth(self) -> str:
        request = self.client.build_request("POST", "/v1/chat/completions", json={"model": "smoke-no-model"})
        del request.headers["Authorization"]
        response = self.client.send(request)
        if response.status_code != 401:
            raise CheckFailed(f"a request without a key got HTTP {response.status_code}, expected 401")
        return "a request without a key is rejected with 401"

    def models(self, out: list[dict[str, Any]]) -> str:
        data = json_body(expect_ok(self.client.get("/v1/models"))).get("data")
        if not isinstance(data, list) or not data:
            raise CheckFailed("the router lists no models")
        out.extend(entry for entry in data if isinstance(entry, dict))
        routes = {entry["x_router"].get("route") for entry in out if isinstance(entry.get("x_router"), dict)}
        return f"{len(out)} model id(s) on {len(routes)} route(s)"

    def chat(self, model: str) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": self.args.max_tokens,
        }
        body = json_body(expect_ok(self.client.post("/v1/chat/completions", json=payload)))
        choices = body.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        if not isinstance(message, dict):
            raise CheckFailed("the answer has no choices[0].message")
        reasoning = has_reasoning(message)
        if not message.get("content") and not reasoning:
            raise CheckFailed("the answer has neither content nor reasoning")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        tokens = f", {usage['completion_tokens']} completion tokens" if "completion_tokens" in usage else ""
        return f"reasoning field: {'yes' if reasoning else 'no'}{tokens}"

    def chat_stream(self, model: str) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": self.args.max_tokens,
            "stream": True,
        }
        started = time.perf_counter()
        first_byte: float | None = None
        chunks, text_seen, reasoning, done = 0, False, False, False
        with self.client.stream("POST", "/v1/chat/completions", json=payload) as response:
            if response.status_code != 200:
                response.read()
                raise CheckFailed(error_text(response))
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("text/event-stream"):
                raise CheckFailed(f"expected an event stream, got content type {content_type!r}")
            for line in response.iter_lines():
                if first_byte is None:
                    first_byte = time.perf_counter() - started
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    continue
                try:
                    event = json.loads(data)
                except ValueError:
                    raise CheckFailed(f"an event is not JSON: {data[:80]!r}") from None
                if not isinstance(event, dict):
                    raise CheckFailed(f"an event is not a JSON object: {data[:80]!r}")
                if event.get("error"):
                    error = event["error"]
                    message = error.get("message") if isinstance(error, dict) else error
                    raise CheckFailed(f"error event in the stream: {str(message)[:200]}")
                chunks += 1
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") if isinstance(choice, dict) else None
                    if isinstance(delta, dict):
                        text_seen = text_seen or bool(delta.get("content"))
                        reasoning = reasoning or has_reasoning(delta)
        if not done:
            raise CheckFailed(f"the stream ended without [DONE] after {chunks} event(s)")
        if not text_seen and not reasoning:
            raise CheckFailed(f"{chunks} event(s), but none with content or reasoning")
        return f"{chunks} events, first byte after {first_byte or 0:.2f} s, reasoning field: " + (
            "yes" if reasoning else "no"
        )

    def embeddings(self, model: str) -> str:
        payload = {"model": model, "input": ["smoke test"]}
        body = json_body(expect_ok(self.client.post("/v1/embeddings", json=payload)))
        data = body.get("data")
        vector = data[0].get("embedding") if isinstance(data, list) and data else None
        if not isinstance(vector, list) or not vector:
            raise CheckFailed("the answer has no data[0].embedding")
        return f"{len(vector)} dimensions"

    def rerank(self, model: str) -> str:
        payload = {
            "model": model,
            "query": "What is the capital of France?",
            "documents": ["Paris is the capital of France.", "Bread is baked.", "Berlin is in Germany."],
            "top_n": 2,
        }
        body = json_body(expect_ok(self.client.post("/v1/rerank", json=payload)))
        results = body.get("results")
        if not isinstance(results, list) or not results:
            raise CheckFailed("the answer has no results")
        return f"{len(results)} result(s) for top_n=2"

    def voices(self, model: str) -> str:
        response = self.client.get("/v1/audio/voices", params={"model": model})
        from_backend = response.headers.get("x-router-error-source") == "backend"
        if response.status_code in (404, 405) and from_backend:
            raise Skipped(f"this backend has no voice list (HTTP {response.status_code})")
        names = voice_names(json_body(expect_ok(response)))
        if not names:
            raise CheckFailed("the voice list is empty or has an unknown shape")
        self.voice = self.voice or names[0]
        return f"{len(names)} voice(s), using {self.voice!r}"

    def speech(self, model: str) -> str:
        payload = {"model": model, "input": SPEECH_TEXT, "response_format": "wav"}
        if self.voice:
            payload["voice"] = self.voice
        response = expect_ok(self.client.post("/v1/audio/speech", json=payload))
        content_type = response.headers.get("content-type", "application/octet-stream")
        if not response.content:
            raise CheckFailed("the answer is empty")
        if content_type.startswith("application/json"):
            raise CheckFailed(f"expected audio, got JSON: {response.text[:120]}")
        self.audio = self.audio or (response.content, content_type)
        return f"{len(response.content)} bytes of {content_type}"

    def transcription(self, model: str) -> str:
        if self.audio is not None:
            content, content_type = self.audio
            source = "the speech above"
        else:
            content, content_type, source = silence_wav(), "audio/wav", "one second of silence"
        extension = "mp3" if "mpeg" in content_type or "mp3" in content_type else "wav"
        response = self.client.post(
            "/v1/audio/transcriptions",
            data={"model": model},
            files={"file": (f"smoke.{extension}", content, content_type)},
        )
        text = json_body(expect_ok(response)).get("text")
        if not isinstance(text, str):
            raise CheckFailed("the answer has no text")
        shown = " ".join(text.split())
        shown = shown[:40] + "..." if len(shown) > 40 else shown
        return f"of {source}: {shown!r}"


# ---- command line --------------------------------------------------------------


def model_option(value: str) -> tuple[str, str]:
    route_type, sep, model_id = value.partition("=")
    if not sep or route_type not in ROUTE_TYPES or not model_id:
        raise argparse.ArgumentTypeError(f"expected TYPE=ID with TYPE one of {', '.join(ROUTE_TYPES)}")
    return route_type, model_id


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smoke.py",
        description="Smoke test for a deployed model router: one cheap call per endpoint type.",
        epilog="Exit code: 0 all checks passed, 1 a check failed, 2 the test could not start.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SMOKE_BASE_URL"),
        help="the router, for example https://router.example.org (env SMOKE_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="an API key of the router. Prefer the environment variable SMOKE_API_KEY, "
        "which does not end up in the shell history",
    )
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--insecure", action="store_true", help="do not verify the TLS certificate")
    tls.add_argument("--ca-file", metavar="FILE", help="CA bundle (PEM) to verify the certificate with")
    parser.add_argument(
        "--timeout", type=float, default=120.0, metavar="S", help="seconds per call (default: 120)"
    )
    parser.add_argument(
        "--model",
        action="append",
        type=model_option,
        default=[],
        metavar="TYPE=ID",
        help="call this model for a route type instead of the first one listed; repeatable. "
        f"TYPE is one of {', '.join(ROUTE_TYPES)}",
    )
    parser.add_argument(
        "--all-routes", action="store_true", help="call one model of every route, not one per route type"
    )
    parser.add_argument(
        "--allow-down", action="store_true", help="do not fail because /health reports a route as down"
    )
    parser.add_argument("--voice", help="voice for text-to-speech (default: the first one listed)")
    parser.add_argument(
        "--max-tokens", type=int, default=256, metavar="N", help="max_tokens of the chat calls (default: 256)"
    )
    parser.add_argument("--json", action="store_true", help="print the rows as JSON instead of a table")
    args = parser.parse_args(argv)
    wanted: dict[str, list[str]] = {}
    for route_type, model_id in args.model:
        wanted.setdefault(route_type, []).append(model_id)
    args.model = wanted
    return args


def render(rows: list[Row]) -> str:
    table = [("CHECK", "MODEL", "RESULT", "TIME", "DETAIL")]
    table += [(r.check, r.model, r.result, f"{r.seconds:.2f}s", r.detail) for r in rows]
    widths = [max(len(line[i]) for line in table) for i in range(4)]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(line[:4], widths, strict=True)) + "  " + line[4]
        for line in table
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = args.api_key or os.environ.get("SMOKE_API_KEY")
    if not args.base_url:
        print("smoke.py: no router given: use --base-url or SMOKE_BASE_URL", file=sys.stderr)
        return 2
    if not api_key:
        print("smoke.py: no API key given: set SMOKE_API_KEY (or use --api-key)", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/").removesuffix("/v1")

    verify: ssl.SSLContext | bool = True
    if args.insecure:
        verify = False
    elif args.ca_file:
        try:
            verify = ssl.create_default_context(cafile=args.ca_file)
        except (OSError, ssl.SSLError) as exc:
            print(f"smoke.py: cannot use --ca-file {args.ca_file}: {exc}", file=sys.stderr)
            return 2

    with httpx.Client(
        base_url=base_url,
        verify=verify,
        timeout=httpx.Timeout(args.timeout, connect=10.0),
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "model-router-smoke"},
    ) as client:
        rows = Smoke(client, args).run()

    failed = sum(1 for row in rows if row.result == FAIL)
    if args.json:
        print(json.dumps({"base_url": base_url, "failed": failed, "rows": [asdict(r) for r in rows]}))
    else:
        note = " (certificate NOT verified)" if args.insecure else ""
        print(f"router: {base_url}{note}")
        print(render(rows))
        passed = sum(1 for row in rows if row.result == OK)
        skipped = sum(1 for row in rows if row.result == SKIP)
        print(f"{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
