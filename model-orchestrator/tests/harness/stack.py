"""A *stack* is one router instance with its own database, default key and HTTP client.

:class:`StackFactory` builds stacks and fakes and tears them down again; the
fixtures in ``tests/conftest.py`` hand out factories at function, module and
session scope.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..fakes import (
    FakeBackend,
    FakeForwardProxy,
    FakeKokoro,
    FakeReranker,
    FakeTEI,
    FakeVLLM,
    FakeWhisper,
    LoopThread,
    TcpRelay,
)
from .postgres import ApiKey, Database, PostgresServer
from .router import RouterProcess
from .util import HOST, sse_data, sse_events, wait_until

ENDPOINTS = {
    "chat": "/v1/chat/completions",
    "vision": "/v1/chat/completions",
    "embedding": "/v1/embeddings",
    "reranker": "/v1/rerank",
    "stt": "/v1/audio/transcriptions",
    "tts": "/v1/audio/speech",
}
FAKES = {
    "vllm": FakeVLLM,
    "tei": FakeTEI,
    "reranker": FakeReranker,
    "whisper": FakeWhisper,
    "kokoro": FakeKokoro,
}
DEFAULT_KEY: Any = object()  # "use the stack's default key"; pass key=None to send no Authorization


def route(
    name: str, type_: str, backend: Any, *, models: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    """One entry of the YAML ``routes`` list.

    ``backend`` is a fake, a URL, or a list of either (a pool). ``endpoint`` is
    filled in from the route type. Anything in ``extra`` is written verbatim,
    e.g. ``backend_model_name=``, ``owned_by=``, ``health_path=None``,
    ``request_defaults={...}``.
    """

    def url(b: Any) -> str:
        return b if isinstance(b, str) else b.url

    entry: dict[str, Any] = {
        "name": name,
        "type": type_,
        "backend": [url(b) for b in backend] if isinstance(backend, list) else url(backend),
        "endpoint": ENDPOINTS[type_],
    }
    if models is not None:
        entry["models"] = list(models)
    entry.update(extra)
    return entry


def user(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


@dataclass
class StreamResult:
    """What a client got from a streaming request, including a broken transfer."""

    status: int
    headers: httpx.Headers
    raw: bytes = b""
    chunks: list[bytes] = field(default_factory=list)
    error: Exception | None = None  # transport error while reading the body
    elapsed: float = 0.0
    first_byte: float | None = None  # seconds until the first body byte

    @property
    def events(self) -> list[bytes]:
        return sse_events(self.raw)

    @property
    def data(self) -> list[Any]:
        return sse_data(self.raw)

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")

    def json(self) -> Any:
        """The body as JSON; an assertion failure (not an error) if it is something else."""
        try:
            return json.loads(self.raw)
        except ValueError:
            raise AssertionError(f"expected a JSON body, got {self.status}: {self.raw[:200]!r}") from None


class Stack:
    def __init__(
        self,
        router: RouterProcess,
        db: Database,
        key: ApiKey | None,
        target: str,
        config: dict[str, Any],
        relay: TcpRelay | None = None,
    ) -> None:
        self.router = router
        self.db = db
        self.key = key  # the default key, seeded before the router started
        self.target = target
        self.config = config  # the YAML document the router was started with
        self.relay = relay  # between router and Postgres, when asked for
        # One connection per request: the legacy router resets a kept-alive connection
        # after some failures, which would otherwise break the *next*, unrelated request.
        self.http = httpx.Client(
            base_url=router.base_url,
            timeout=30.0,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    # ---- plain requests --------------------------------------------------

    def _headers(self, key: Any, headers: dict[str, str] | None) -> dict[str, str]:
        out: dict[str, str] = {}
        if key is DEFAULT_KEY:
            key = self.key
        if isinstance(key, ApiKey):
            out.update(key.auth)
        elif isinstance(key, str):
            out["Authorization"] = f"Bearer {key}"
        out.update(headers or {})
        return out

    def request(
        self,
        method: str,
        path: str,
        *,
        key: Any = DEFAULT_KEY,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return self.http.request(method, path, headers=self._headers(key, headers), **kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def chat(self, model: str | None, text: str = "hello", **kwargs: Any) -> httpx.Response:
        """Non-streaming chat. Extra body fields go in ``body={...}``."""
        body = {"model": model, "messages": user(text), **kwargs.pop("body", {})}
        return self.post("/v1/chat/completions", json=body, **kwargs)

    # ---- streaming -------------------------------------------------------

    def stream(
        self,
        path: str,
        *,
        json: Any,
        key: Any = DEFAULT_KEY,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        on_chunk: Any = None,
    ) -> StreamResult:
        """POST and read the body chunk by chunk. A transfer that breaks is captured, not raised."""
        started = time.monotonic()
        with self.http.stream(
            "POST", path, json=json, headers=self._headers(key, headers), timeout=timeout
        ) as resp:
            result = StreamResult(status=resp.status_code, headers=resp.headers)
            try:
                for chunk in resp.iter_raw():
                    if result.first_byte is None:
                        result.first_byte = time.monotonic() - started
                    result.chunks.append(chunk)
                    result.raw += chunk
                    if on_chunk is not None:
                        on_chunk(result)
            except httpx.TransportError as exc:
                result.error = exc
        result.elapsed = time.monotonic() - started
        return result

    def chat_stream(self, model: str | None, text: str = "hello", **kwargs: Any) -> StreamResult:
        body = {"model": model, "messages": user(text), "stream": True, **kwargs.pop("body", {})}
        return self.stream("/v1/chat/completions", json=body, **kwargs)

    # ---- models and keys -------------------------------------------------

    def models(self) -> dict[str, dict[str, Any]]:
        """``GET /v1/models`` as ``{id: entry}``."""
        response = self.get("/v1/models", key=None)
        assert response.status_code == 200, response.text
        return {entry["id"]: entry for entry in response.json()["data"]}

    def authenticates(self, key: ApiKey | str) -> bool:
        """Does the router accept this key right now? (An unknown model is 404 once auth passed.)"""
        response = self.post("/v1/chat/completions", key=key, json={"model": "e2e-no-such-model"})
        assert response.status_code in (401, 404), f"unexpected {response.status_code}: {response.text[:200]}"
        return response.status_code == 404

    def sync_keys(self, timeout: float = 10.0) -> None:
        """Block until the router has picked up every key row written so far.

        A sentinel key is inserted last; the router loads the key table in one
        query, so once the sentinel authenticates all earlier changes are in.
        """
        sentinel = self.db.create_key(name="sync-sentinel")
        wait_until(
            lambda: self.authenticates(sentinel), timeout=timeout, what="the router to refresh its keys"
        )

    def new_key(self, **kwargs: Any) -> ApiKey:
        """Create a key in Postgres and wait until the router knows it."""
        key = self.db.create_key(**kwargs)
        self.sync_keys()
        return key

    def close(self) -> None:
        self.http.close()
        self.router.stop()
        self.db.drop()


class StackFactory:
    """Builds fakes and stacks, remembers them, and removes them in :meth:`close`."""

    def __init__(
        self, *, target: str, postgres: PostgresServer, loop_thread: LoopThread, tmp_root: Any
    ) -> None:
        self.target = target
        self.postgres = postgres
        self.loop_thread = loop_thread
        self._tmp_root = tmp_root  # pytest's TempPathFactory
        self._stacks: list[Stack] = []
        self._closables: list[Any] = []

    # ---- fakes -----------------------------------------------------------

    def fake(self, kind: str, *args: Any, **kwargs: Any) -> Any:
        backend: FakeBackend = FAKES[kind](self.loop_thread, *args, **kwargs)
        backend.start()
        self._closables.append(backend)
        return backend

    def forward_proxy(self) -> FakeForwardProxy:
        proxy = FakeForwardProxy(self.loop_thread).start()
        self._closables.append(proxy)
        return proxy

    def relay(self, target_host: str, target_port: int, *, tls: Any = None) -> TcpRelay:
        """A TCP relay to a target. ``tls`` (an ``ssl.SSLContext``) makes it an HTTPS front."""
        relay = TcpRelay(self.loop_thread, target_host, target_port, tls=tls).start()
        self._closables.append(relay)
        return relay

    # ---- stacks ----------------------------------------------------------

    def workdir(self, name: str) -> Path:
        return self._tmp_root.mktemp(f"router-{name}", numbered=True)

    def stack(
        self,
        routes: list[dict[str, Any]],
        *,
        name: str = "router",
        env: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        server: dict[str, Any] | None = None,
        schema: str | None = "legacy",
        db_via_relay: bool = False,
        host: str = HOST,
        port: int | None = None,
        start: bool = True,
        target: str | None = None,
    ) -> Stack:
        """Create a database, write the config, start a router and wait for ``/livez``.

        ``schema="legacy"`` (default) prepares the database the way the old
        router left it and seeds one default key before the start, so no test
        has to wait for a key refresh. ``schema=None`` hands the router a
        completely empty database and no key.

        ``config`` adds top-level YAML keys (``upstreams``), ``server``
        replaces the ``server`` block, ``env`` overrides environment variables
        (``None`` unsets one). ``db_via_relay`` puts a :class:`TcpRelay`
        between router and Postgres; it is available as ``stack.relay``.
        ``target`` starts another implementation than the session's
        (``"legacy"`` as the old router behind a new one).
        """
        target = target or self.target
        db = self.postgres.create_database()
        key = None
        if schema == "legacy":
            db.create_legacy_schema()
            key = db.create_key(name="default")
        database_url, relay = db.url, None
        if db_via_relay:
            relay = self.relay(self.postgres.host, self.postgres.port)
            database_url = db.url_via(relay.port)
        document = {
            "server": server if server is not None else {"host": HOST, "port": 8090},
            "routes": routes,
        }
        document.update(config or {})
        router = RouterProcess(
            name=name,
            target=target,
            workdir=self.workdir(name),
            database_url=database_url,
            config=document,
            env=env,
            host=host,
            port=port,
        )
        stack = Stack(router, db, key, target, document, relay)
        self._stacks.append(stack)
        if start:
            router.start()
        return stack

    def close(self) -> None:
        for backend in self._closables:
            if isinstance(backend, FakeBackend):
                backend.clear_faults()  # unblock anything a router is still waiting on
                backend.resume_stream()
        for stack in reversed(self._stacks):
            stack.close()
        self._stacks.clear()
        for closable in reversed(self._closables):
            closable.close()
        self._closables.clear()


__all__ = [
    "DEFAULT_KEY",
    "ENDPOINTS",
    "HOST",
    "Stack",
    "StackFactory",
    "StreamResult",
    "route",
    "sse_data",
    "sse_events",
    "user",
]
