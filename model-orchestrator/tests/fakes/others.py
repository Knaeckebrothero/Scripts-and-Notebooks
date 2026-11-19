"""The non-chat fakes: TEI-like embeddings, a reranker, Whisper-like STT, Kokoro-like TTS."""

from __future__ import annotations

import hashlib
from typing import Any

from aiohttp import web

from .base import FakeBackend, LoopThread, current, dumps
from .vllm import model_entry


class FakeTEI(FakeBackend):
    """Text Embeddings Inference: ``/v1/embeddings`` with ``usage``, 422 above 64 inputs.

    Like the real server it has ``/health`` but no ``/v1/models``, and reports
    errors with a top-level ``message`` (not the OpenAI ``error`` object).
    """

    kind = "tei"
    max_batch = 64

    def _add_routes(self, router: web.UrlDispatcher) -> None:
        router.add_post("/v1/embeddings", self._embeddings)

    @staticmethod
    def vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [round(b / 255, 4) for b in digest[:4]]

    @staticmethod
    def tokens(inputs: list[str]) -> int:
        return sum(len(t.split()) + 1 for t in inputs)

    @classmethod
    def too_many(cls, count: int) -> bytes:
        message = f"batch size {count} > maximum allowed batch size {cls.max_batch}"
        return dumps({"message": message, "code": 422, "type": "Validation"}).encode()

    async def _embeddings(self, request: web.Request) -> web.StreamResponse:
        payload = current().json
        if not isinstance(payload, dict) or "input" not in payload:
            body = {"message": "missing field `input`", "code": 422, "type": "Validation"}
            return await self._reply(request, 422, dumps(body).encode(), "application/json")
        raw = payload["input"]
        inputs = [raw] if isinstance(raw, str) else [str(x) for x in raw]
        if len(inputs) > self.max_batch:
            return await self._reply(request, 422, self.too_many(len(inputs)), "application/json")
        tokens = self.tokens(inputs)
        body = {
            "object": "list",
            "data": [
                {"object": "embedding", "embedding": self.vector(t), "index": i} for i, t in enumerate(inputs)
            ],
            "model": payload.get("model"),
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }
        return await self._reply(request, 200, dumps(body).encode(), "application/json")


class FakeReranker(FakeBackend):
    """A vLLM-style reranker: ``/v1/rerank`` honours ``top_n`` and returns ``usage``.

    ``top_n: null`` makes it answer 500, like the backends that made the router
    strip the field in the first place.
    """

    kind = "reranker"

    def __init__(self, loop_thread: LoopThread, models: list[str] | None = None, name: str | None = None):
        super().__init__(loop_thread, name)
        self.models = [model_entry(m) for m in (models or ["acme/fake-rerank"])]

    def _add_routes(self, router: web.UrlDispatcher) -> None:
        router.add_get("/v1/models", self._models)
        router.add_post("/v1/rerank", self._rerank)

    async def _models(self, request: web.Request) -> web.StreamResponse:
        return await self._reply(
            request, 200, dumps({"object": "list", "data": self.models}).encode(), "application/json"
        )

    @staticmethod
    def score(query: str, document: str) -> float:
        digest = hashlib.sha256(f"{query}\x00{document}".encode()).digest()
        return round(int.from_bytes(digest[:4], "big") / 2**32, 6)

    @staticmethod
    def tokens(query: str, documents: list[str]) -> int:
        return sum(len(query.split()) + len(d.split()) for d in documents)

    async def _rerank(self, request: web.Request) -> web.StreamResponse:
        payload = current().json
        if not isinstance(payload, dict) or "query" not in payload or "documents" not in payload:
            body = {
                "error": {
                    "message": "query and documents are required",
                    "type": "BadRequestError",
                    "code": 400,
                }
            }
            return await self._reply(request, 400, dumps(body).encode(), "application/json")
        if "top_n" in payload and payload["top_n"] is None:
            body = {
                "error": {"message": "top_n must be an integer", "type": "InternalServerError", "code": 500}
            }
            return await self._reply(request, 500, dumps(body).encode(), "application/json")
        query, documents = str(payload["query"]), [str(d) for d in payload["documents"]]
        results = [
            {"index": i, "document": {"text": d}, "relevance_score": self.score(query, d)}
            for i, d in enumerate(documents)
        ]
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        if isinstance(payload.get("top_n"), int):
            results = results[: payload["top_n"]]
        body = {
            "id": "rerank-fake",
            "model": payload.get("model"),
            "usage": {"total_tokens": self.tokens(query, documents)},
            "results": results,
        }
        return await self._reply(request, 200, dumps(body).encode(), "application/json")


class FakeWhisper(FakeBackend):
    """A Whisper-like server: ``/v1/audio/transcriptions`` echoes the form it received.

    ``response_format=text`` (or srt/vtt) answers plain text like the real API,
    which makes a dropped form field visible to the client. No ``/health`` and
    no ``/v1/models``, like the stock Whisper image.
    """

    kind = "whisper"

    def _add_routes(self, router: web.UrlDispatcher) -> None:
        router.add_post("/v1/audio/transcriptions", self._transcribe)

    async def _health(self, request: web.Request) -> web.StreamResponse:
        return await self._not_found(request)

    @staticmethod
    def transcript(filename: str | None, size: int) -> str:
        return f"transcript of {filename} ({size} bytes)"

    async def _transcribe(self, request: web.Request) -> web.StreamResponse:
        rec = current()
        if rec.form is None:
            return await self._reply(
                request, 400, b'{"detail":"expected multipart/form-data"}', "application/json"
            )
        upload = rec.form_file("file")
        if upload is None:
            return await self._reply(request, 422, b'{"detail":"Field required: file"}', "application/json")
        text = self.transcript(upload.filename, upload.size)
        fmt = (rec.form_values("response_format") or ["json"])[-1]
        if fmt in ("text", "srt", "vtt"):
            return await self._reply(request, 200, text.encode(), "text/plain; charset=utf-8")
        body: dict[str, Any] = {"text": text, "fake": {"fields": [f.as_dict() for f in rec.form]}}
        if fmt == "verbose_json":
            body["language"] = (rec.form_values("language") or ["en"])[-1]
            body["duration"] = 1.25
        return await self._reply(request, 200, dumps(body).encode(), "application/json")


class FakeKokoro(FakeBackend):
    """A Kokoro-like server: ``/v1/audio/speech`` returns bytes, ``/v1/audio/voices`` a list.

    No ``/health`` and no ``/v1/models``.
    """

    kind = "kokoro"
    voices = ["af_heart", "af_bella", "bm_george"]
    _types = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus", "pcm": "audio/pcm"}

    def _add_routes(self, router: web.UrlDispatcher) -> None:
        router.add_post("/v1/audio/speech", self._speech)
        router.add_get("/v1/audio/voices", self._voices)

    async def _health(self, request: web.Request) -> web.StreamResponse:
        return await self._not_found(request)

    @staticmethod
    def audio(text: str) -> bytes:
        """Deterministic 'audio': every byte value occurs, so nothing may be re-encoded."""
        return b"ID3\x00" + hashlib.sha256(text.encode()).digest() + bytes(range(256)) + b"\r\n\x00\xff"

    async def _speech(self, request: web.Request) -> web.StreamResponse:
        payload = current().json
        if not isinstance(payload, dict) or not isinstance(payload.get("input"), str):
            return await self._reply(request, 400, b'{"detail":"input is required"}', "application/json")
        ctype = self._types.get(str(payload.get("response_format", "mp3")), "audio/mpeg")
        return await self._reply(request, 200, self.audio(payload["input"]), ctype)

    async def _voices(self, request: web.Request) -> web.StreamResponse:
        return await self._reply(request, 200, dumps({"voices": self.voices}).encode(), "application/json")
