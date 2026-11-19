"""The default set of fakes and routes that most tests share (session scope).

Names are made up. Each constant is used by tests, so a rename happens in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..fakes import FakeKokoro, FakeReranker, FakeTEI, FakeVLLM, FakeWhisper
from .stack import StackFactory, route

# chat: two aliases, a backend that also serves an id no alias points at
CHAT_ROUTE = "Chat-Alpha"
CHAT_ALIAS = "chat-alpha"
CHAT_ALIAS_2 = "chat-alpha-latest"
CHAT_BACKEND_MODEL = "acme/chat-alpha-7b"
CHAT_DISCOVERED = "acme/chat-alpha-7b-lora"

# chat with request_defaults
REASONER_ROUTE = "Reasoner-Gamma"
REASONER_ALIAS = "reasoner-gamma"
REASONER_BACKEND_MODEL = "acme/reasoner-9b"
REASONER_DEFAULTS: dict[str, Any] = {
    "skip_special_tokens": False,
    "temperature": 0.7,
    "chat_template_kwargs": {"enable_thinking": True, "reasoning_strength": "high"},
}

VISION_ROUTE = "Vision-Beta"
VISION_ALIAS = "vision-beta"
VISION_BACKEND_MODEL = "acme/vision-beta-12b"

EMBED_ROUTE = "Embed-Delta"
EMBED_ALIAS = "embed-delta"
EMBED_BACKEND_MODEL = "acme/embed-delta"

RERANK_ROUTE = "Rerank-Epsilon"
RERANK_ALIAS = "rerank-epsilon"
RERANK_BACKEND_MODEL = "acme/rerank-epsilon"

STT_ROUTE = "Whisper-Zeta"
STT_ALIAS = "whisper-zeta"
STT_ALIAS_2 = "stt-1"
STT_BACKEND_MODEL = "acme/whisper-zeta"

TTS_ROUTE = "Kokoro-Eta"
TTS_ALIAS = "kokoro-eta"
TTS_ALIAS_2 = "tts-1"
TTS_BACKEND_MODEL = "kokoro"

OWNED_BY = "acme-labs"  # set on the chat route only; the others show what the backend reports

# route name -> (type, one alias)
ROUTE_TYPES = {
    CHAT_ROUTE: ("chat", CHAT_ALIAS),
    REASONER_ROUTE: ("chat", REASONER_ALIAS),
    VISION_ROUTE: ("vision", VISION_ALIAS),
    EMBED_ROUTE: ("embedding", EMBED_ALIAS),
    RERANK_ROUTE: ("reranker", RERANK_ALIAS),
    STT_ROUTE: ("stt", STT_ALIAS),
    TTS_ROUTE: ("tts", TTS_ALIAS),
}
ALL_ALIASES = [
    CHAT_ALIAS, CHAT_ALIAS_2, REASONER_ALIAS, VISION_ALIAS, EMBED_ALIAS,
    RERANK_ALIAS, STT_ALIAS, STT_ALIAS_2, TTS_ALIAS, TTS_ALIAS_2,
]  # fmt: skip
ALL_DISCOVERED = [
    CHAT_BACKEND_MODEL, CHAT_DISCOVERED, REASONER_BACKEND_MODEL, VISION_BACKEND_MODEL, RERANK_BACKEND_MODEL,
]  # fmt: skip


@dataclass
class World:
    chat: FakeVLLM
    reasoner: FakeVLLM
    vision: FakeVLLM
    embed: FakeTEI
    rerank: FakeReranker
    whisper: FakeWhisper
    kokoro: FakeKokoro

    @classmethod
    def build(cls, factory: StackFactory) -> World:
        return cls(
            chat=factory.fake("vllm", [CHAT_BACKEND_MODEL, CHAT_DISCOVERED], name="chat"),
            reasoner=factory.fake("vllm", [REASONER_BACKEND_MODEL], name="reasoner"),
            vision=factory.fake("vllm", [VISION_BACKEND_MODEL], name="vision"),
            embed=factory.fake("tei", name="embed"),
            rerank=factory.fake("reranker", [RERANK_BACKEND_MODEL], name="rerank"),
            whisper=factory.fake("whisper", name="whisper"),
            kokoro=factory.fake("kokoro", name="kokoro"),
        )

    @property
    def fakes(self) -> list[Any]:
        return [self.chat, self.reasoner, self.vision, self.embed, self.rerank, self.whisper, self.kokoro]

    def reset(self) -> None:
        for fake in self.fakes:
            fake.reset()

    def routes(self) -> list[dict[str, Any]]:
        return [
            route(
                CHAT_ROUTE, "chat", self.chat,
                backend_model_name=CHAT_BACKEND_MODEL, models=[CHAT_ALIAS, CHAT_ALIAS_2], owned_by=OWNED_BY,
            ),
            route(
                REASONER_ROUTE, "chat", self.reasoner,
                backend_model_name=REASONER_BACKEND_MODEL, models=[REASONER_ALIAS],
                request_defaults=REASONER_DEFAULTS,
            ),
            route(
                VISION_ROUTE, "vision", self.vision,
                backend_model_name=VISION_BACKEND_MODEL, models=[VISION_ALIAS],
            ),
            route(
                EMBED_ROUTE, "embedding", self.embed,
                backend_model_name=EMBED_BACKEND_MODEL, models=[EMBED_ALIAS],
            ),
            route(
                RERANK_ROUTE, "reranker", self.rerank,
                backend_model_name=RERANK_BACKEND_MODEL, models=[RERANK_ALIAS],
            ),
            route(
                STT_ROUTE, "stt", self.whisper,
                backend_model_name=STT_BACKEND_MODEL, models=[STT_ALIAS, STT_ALIAS_2], health_path=None,
            ),
            route(
                TTS_ROUTE, "tts", self.kokoro,
                backend_model_name=TTS_BACKEND_MODEL, models=[TTS_ALIAS, TTS_ALIAS_2], health_path=None,
            ),
        ]  # fmt: skip
