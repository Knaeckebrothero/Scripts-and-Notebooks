"""Fake model backends on fixed ports, for the local demo stack.

    uv run python scripts/demo_backends.py                  # 127.0.0.1:9001-9007
    uv run python scripts/demo_backends.py --host 0.0.0.0   # what demo/backends.Dockerfile runs

These are the fakes of the test suite (``tests/fakes``): real HTTP servers that
answer like vLLM, Text Embeddings Inference, a reranker, Whisper and Kokoro,
with made-up content and no GPU. ``demo/config.yaml`` is the route config that
goes with them, and ``docker-compose.yaml`` starts both next to a router.

Needs aiohttp, which is in the ``dev`` dependency group. Not for production.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the project root, for tests.fakes

from tests.fakes import (  # noqa: E402
    FakeBackend,
    FakeKokoro,
    FakeReranker,
    FakeTEI,
    FakeVLLM,
    FakeWhisper,
    LoopThread,
)

CHAT_MODEL = "demo/chat-7b"
VISION_MODEL = "demo/vision-12b"
RERANK_MODEL = "demo/rerank-base"

# (port offset, what it stands in for, class, constructor arguments). Two chat
# servers with the same model, so the demo config can show a pool.
BACKENDS: tuple[tuple[int, str, type[FakeBackend], tuple[Any, ...]], ...] = (
    (0, "chat, pool member 1 (vLLM)", FakeVLLM, ([CHAT_MODEL],)),
    (1, "chat, pool member 2 (vLLM)", FakeVLLM, ([CHAT_MODEL],)),
    (2, "vision (vLLM)", FakeVLLM, ([VISION_MODEL],)),
    (3, "embeddings (TEI)", FakeTEI, ()),
    (4, "rerank", FakeReranker, ([RERANK_MODEL],)),
    (5, "speech-to-text (Whisper)", FakeWhisper, ()),
    (6, "text-to-speech (Kokoro)", FakeKokoro, ()),
)
# The fakes record every request for the tests to inspect. Nobody reads that
# here, so it is thrown away at this interval instead of growing for ever.
FORGET_EVERY_S = 30.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fake model backends on fixed ports (demo only).")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    parser.add_argument(
        "--base-port", type=int, default=9001, help="first of seven consecutive ports (default: 9001)"
    )
    args = parser.parse_args(argv)

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())

    loop = LoopThread()
    running: list[FakeBackend] = []
    try:
        for offset, what, cls, cls_args in BACKENDS:
            fake = cls(loop, *cls_args, name=what)
            fake.host, fake.port = args.host, args.base_port + offset
            try:
                fake.start()
            except OSError as exc:
                print(f"cannot listen on {args.host}:{fake.port}: {exc.strerror or exc}", file=sys.stderr)
                return 1
            running.append(fake)
            print(f"{fake.url:<24} {what}", flush=True)
        print("fake backends are up; stop with Ctrl-C", flush=True)
        while not stop.wait(FORGET_EVERY_S):
            for fake in running:
                fake.clear_requests()
    finally:
        for fake in reversed(running):
            fake.close()
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
