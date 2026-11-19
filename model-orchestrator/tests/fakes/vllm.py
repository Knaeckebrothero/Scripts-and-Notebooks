"""A vLLM-like server: chat completions (plain and SSE), ``/v1/models``, ``/health``.

Responses are a pure function of the request, so a test can compute the exact
bytes a client must receive with :meth:`FakeVLLM.render_completion` and
:meth:`FakeVLLM.render_stream`. Like vLLM, the stream only carries the
usage-only event (empty ``choices``, ``usage`` present) when the request sets
``stream_options.include_usage``.

The bodies are written by hand rather than with ``json.dumps`` and mix escaped
and raw non-ASCII, an oversized integer and a float with a trailing zero. A
router that parses and re-serialises a response instead of relaying it cannot
reproduce them byte for byte.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from .base import FakeBackend, LoopThread, current, dumps

CREATED = 1767225600
_X_FAKE = (
    '"x_fake":{"escaped":"caf\\u00e9","raw":"café ✓","float":1.50,'
    '"big":12345678901234567890123,"nested":{"list":[1,null,true]}}'
)


def model_entry(model_id: str, **extra: Any) -> dict[str, Any]:
    """A ``/v1/models`` entry the way vLLM reports it, ``max_model_len`` included."""
    entry = {
        "id": model_id,
        "object": "model",
        "created": CREATED,
        "owned_by": "vllm",
        "root": model_id,
        "parent": None,
        "max_model_len": 32768,
        "permission": [
            {
                "id": "modelperm-fake",
                "object": "model_permission",
                "created": CREATED,
                "allow_create_engine": False,
                "allow_sampling": True,
                "allow_logprobs": True,
                "allow_search_indices": False,
                "allow_view": True,
                "allow_fine_tuning": False,
                "organization": "*",
                "group": None,
                "is_blocking": False,
            }
        ],
    }
    entry.update(extra)
    return entry


def _last_user_text(payload: dict[str, Any]) -> str:
    text = ""
    for message in payload.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return text[:200]


class FakeVLLM(FakeBackend):
    kind = "vllm"

    def __init__(self, loop_thread: LoopThread, models: list[str] | None = None, name: str | None = None):
        super().__init__(loop_thread, name)
        self._default_models = list(models or ["acme/fake-chat"])
        self.models: list[dict[str, Any]] = []
        self.stream_gap = 0.0
        self.stream_script: list[bytes] | None = None
        self._reset_state()

    def _reset_state(self) -> None:
        self.models = [model_entry(m) for m in self._default_models]
        self.stream_gap = 0.0
        self.stream_script = None

    @property
    def model_ids(self) -> list[str]:
        return [m["id"] for m in self.models]

    def add_model(self, model_id: str, **extra: Any) -> None:
        self.models = [*self.models, model_entry(model_id, **extra)]

    def remove_model(self, model_id: str) -> None:
        self.models = [m for m in self.models if m["id"] != model_id]

    # ---- deterministic rendering ----------------------------------------

    @staticmethod
    def parts(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
        """(reasoning pieces, content pieces) for a request."""
        reasoning = ["Let me think", " about it. 思考"]
        content = ["Echo", ": ", _last_user_text(payload) or "(empty)", " ✓ grüße"]
        return reasoning, content

    @classmethod
    def usage(cls, payload: dict[str, Any]) -> dict[str, int]:
        reasoning, content = cls.parts(payload)
        prompt = 3 + sum(len(str(m.get("content", "")).split()) for m in payload.get("messages") or [])
        completion = len(reasoning) + len(content)
        return {"prompt_tokens": prompt, "total_tokens": prompt + completion, "completion_tokens": completion}

    @classmethod
    def render_completion(cls, payload: dict[str, Any]) -> bytes:
        reasoning, content = cls.parts(payload)
        usage = cls.usage(payload)
        # A JSON template full of braces: percent formatting keeps it readable.
        return (
            '{"id":"chatcmpl-fake","object":"chat.completion","created":%d,"model":%s,'  # noqa: UP031
            '"choices":[{"index":0,"message":{"role":"assistant","content":%s,"refusal":null,'
            '"annotations":null,"audio":null,"function_call":null,"tool_calls":[],"reasoning":%s},'
            '"logprobs":null,"finish_reason":"stop","stop_reason":null}],'
            '"service_tier":null,"system_fingerprint":null,'
            '"usage":{"prompt_tokens":%d,"total_tokens":%d,"completion_tokens":%d,"prompt_tokens_details":null},'
            '"prompt_logprobs":null,"kv_transfer_params":null,%s}'
            % (
                CREATED,
                dumps(payload.get("model")),
                dumps("".join(content)),
                dumps("".join(reasoning)),
                usage["prompt_tokens"],
                usage["total_tokens"],
                usage["completion_tokens"],
                _X_FAKE,
            )
        ).encode()

    @classmethod
    def render_stream(cls, payload: dict[str, Any], include_usage: bool | None = None) -> list[bytes]:
        """The SSE events for a request, one ``bytes`` per event, ``[DONE]`` last.

        ``include_usage=None`` follows the request's own ``stream_options``.
        """
        if include_usage is None:
            options = payload.get("stream_options")
            include_usage = bool(isinstance(options, dict) and options.get("include_usage"))
        model = dumps(payload.get("model"))
        head = f'data: {{"id":"chatcmpl-fake","object":"chat.completion.chunk","created":{CREATED},"model":{model},'

        def event(delta: dict[str, Any], finish: str | None = None) -> bytes:
            choice = '"choices":[{"index":0,"delta":%s,"logprobs":null,"finish_reason":%s}]}' % (  # noqa: UP031
                dumps(delta),
                dumps(finish),
            )
            return (head + choice + "\n\n").encode()

        reasoning, content = cls.parts(payload)
        events = [event({"role": "assistant", "content": ""})]
        events += [event({"reasoning": piece}) for piece in reasoning]
        events += [event({"content": piece}) for piece in content]
        events.append(event({"content": ""}, "stop"))
        if include_usage:
            events.append((head + f'"choices":[],"usage":{dumps(cls.usage(payload))}}}\n\n').encode())
        events.append(b"data: [DONE]\n\n")
        return events

    # ---- endpoints -------------------------------------------------------

    def _add_routes(self, router: web.UrlDispatcher) -> None:
        router.add_get("/v1/models", self._models)
        router.add_post("/v1/chat/completions", self._chat)

    async def _models(self, request: web.Request) -> web.StreamResponse:
        return await self._reply(
            request, 200, dumps({"object": "list", "data": self.models}).encode(), "application/json"
        )

    async def _error(self, request: web.Request, status: int, kind: str, message: str) -> web.StreamResponse:
        body = {"error": {"message": message, "type": kind, "param": None, "code": status}}
        return await self._reply(request, status, dumps(body).encode(), "application/json")

    async def _chat(self, request: web.Request) -> web.StreamResponse:
        payload = current().json
        if not isinstance(payload, dict):
            return await self._error(request, 400, "BadRequestError", "request body must be a JSON object")
        if payload.get("model") not in self.model_ids:
            return await self._error(
                request, 404, "NotFoundError", f"The model `{payload.get('model')}` does not exist."
            )
        if not isinstance(payload.get("messages"), list) or not payload["messages"]:
            return await self._error(request, 400, "BadRequestError", "messages: Field required")
        if payload.get("stream"):
            chunks = self.stream_script if self.stream_script is not None else self.render_stream(payload)
            return await self._stream(request, chunks, gap=self.stream_gap)
        return await self._reply(request, 200, self.render_completion(payload), "application/json")
