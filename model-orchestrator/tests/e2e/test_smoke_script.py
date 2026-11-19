"""``scripts/smoke.py``, the live smoke script (design section 8, item 4), run against the fake stack.

The script is started the way an operator starts it: as its own process, with
the key in the environment.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import ssl
import subprocess
import sys
from typing import Any

import pytest

from tests.fakes import FakeKokoro
from tests.harness import world as w
from tests.harness.router import ROUTER_DIR, base_environment
from tests.harness.stack import route
from tests.harness.util import HOST, wait_until

pytestmark = pytest.mark.feature("smoke_script")

SCRIPT = ROUTER_DIR / "scripts" / "smoke.py"
SPEECH_TEXT = "This is a smoke test."  # what the script asks the text-to-speech model to say
ALL_CHECKS = [
    "health", "auth", "models", "chat", "chat, stream", "vision chat", "vision chat, stream",
    "embeddings", "rerank", "voices", "speech", "transcription",
]  # fmt: skip


def smoke(base_url: str | None, key: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    env = base_environment()
    if key is not None:
        env["SMOKE_API_KEY"] = key
    command = [sys.executable, str(SCRIPT), *args]
    if base_url is not None:
        command += ["--base-url", base_url]
    return subprocess.run(command, cwd=ROUTER_DIR, env=env, capture_output=True, text=True, timeout=60)


def rows_of(done: subprocess.CompletedProcess[str]) -> dict[str, dict[str, Any]]:
    """The rows of a ``--json`` run, by check name (the first row of each name)."""
    try:
        report = json.loads(done.stdout)
    except ValueError:
        raise AssertionError(f"no JSON on stdout: {done.stdout!r} / {done.stderr!r}") from None
    rows: dict[str, dict[str, Any]] = {}
    for row in report["rows"]:
        rows.setdefault(row["check"], row)
    return rows


# ---- a healthy router ---------------------------------------------------------


def test_every_endpoint_type_is_called_and_passes(stack, world):
    done = smoke(stack.router.base_url, stack.key.token, "--json")

    rows = rows_of(done)
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["failed"] == 0
    assert list(rows) == ALL_CHECKS
    assert {row["result"] for row in rows.values()} == {"ok"}, rows
    # One model per route type: the first one the router lists.
    assert rows["chat"]["model"] == w.CHAT_ALIAS
    assert rows["vision chat"]["model"] == w.VISION_ALIAS
    assert rows["transcription"]["model"] == w.STT_ALIAS
    # The fakes answer with a reasoning field, and the script says so.
    assert "reasoning field: yes" in rows["chat"]["detail"]
    assert "reasoning field: yes" in rows["chat, stream"]["detail"]


def test_the_calls_reach_the_backends(stack, world):
    smoke(stack.router.base_url, stack.key.token, "--json")

    assert world.chat.last("/v1/chat/completions").json["stream"] is True
    assert world.embed.calls("/v1/embeddings")
    assert world.rerank.last("/v1/rerank").json["top_n"] == 2
    assert world.kokoro.last("/v1/audio/speech").json["voice"] == FakeKokoro.voices[0]
    # Speech-to-text gets exactly the audio that text-to-speech returned.
    upload = world.whisper.last("/v1/audio/transcriptions").form_file("file")
    assert upload is not None
    assert upload.sha256 == hashlib.sha256(FakeKokoro.audio(SPEECH_TEXT)).hexdigest()


def test_the_table_is_printed_and_the_key_is_not(stack, world):
    done = smoke(stack.router.base_url + "/v1/", stack.key.token)

    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == f"router: {stack.router.base_url}"  # a trailing /v1 is accepted
    assert lines[1].split() == ["CHECK", "MODEL", "RESULT", "TIME", "DETAIL"]
    assert lines[-1] == "12 passed, 0 failed, 0 skipped"
    assert stack.key.token not in done.stdout + done.stderr


def test_all_routes_calls_one_model_of_every_route(stack, world):
    done = smoke(stack.router.base_url, stack.key.token, "--json", "--all-routes")

    assert done.returncode == 0, done.stdout + done.stderr
    chat_models = [r["model"] for r in json.loads(done.stdout)["rows"] if r["check"] == "chat"]
    assert chat_models == [w.CHAT_ALIAS, w.REASONER_ALIAS]
    assert world.reasoner.calls("/v1/chat/completions")


def test_a_model_can_be_named(stack, world):
    done = smoke(stack.router.base_url, stack.key.token, "--json", "--model", f"chat={w.REASONER_ALIAS}")

    assert done.returncode == 0, done.stdout + done.stderr
    assert rows_of(done)["chat"]["model"] == w.REASONER_ALIAS
    assert not world.chat.calls("/v1/chat/completions")


# ---- failures set the exit code ---------------------------------------------------


def test_a_wrong_key_fails(stack, world):
    done = smoke(stack.router.base_url, "sk-mo-not-a-key", "--json")

    rows = rows_of(done)
    assert done.returncode == 1
    assert rows["health"]["result"] == "ok"  # open endpoints still answer
    assert rows["chat"]["result"] == "FAIL"
    assert "HTTP 401" in rows["chat"]["detail"]
    assert "request id" in rows["chat"]["detail"]


def test_a_failing_backend_fails_its_row_only(factory):
    chat = factory.fake("vllm", ["acme/solo"])
    rerank = factory.fake("reranker", ["acme/rerank"])
    stack = factory.stack(
        [
            route("Solo", "chat", chat, models=["solo"], backend_model_name="acme/solo"),
            route("Rerank", "reranker", rerank, models=["rr"], backend_model_name="acme/rerank"),
        ]
    )
    rerank.fail(500, {"error": {"message": "CUDA out of memory", "type": "InternalServerError"}})

    done = smoke(stack.router.base_url, stack.key.token, "--json")

    rows = rows_of(done)
    assert done.returncode == 1
    assert rows["chat"]["result"] == "ok"
    assert rows["rerank"]["result"] == "FAIL"
    assert "HTTP 500" in rows["rerank"]["detail"]
    assert "CUDA out of memory" in rows["rerank"]["detail"]
    assert "from the backend" in rows["rerank"]["detail"]
    # Types the router does not offer are skipped, not failed.
    assert rows["embedding"]["result"] == "skip"


def test_a_route_that_is_down_fails_the_health_row(factory):
    chat = factory.fake("vllm", ["acme/solo"])
    embed = factory.fake("tei")
    stack = factory.stack(
        [
            route("Solo", "chat", chat, models=["solo"], backend_model_name="acme/solo"),
            route("Embed", "embedding", embed, models=["emb"]),
        ]
    )
    embed.stop()
    wait_until(
        lambda: stack.get("/health", key=None).json()["routes"]["Embed"] == "down",
        timeout=10,
        what="the router to see the embedding backend down",
    )

    done = smoke(stack.router.base_url, stack.key.token, "--json")
    rows = rows_of(done)
    assert done.returncode == 1
    assert rows["health"]["result"] == "FAIL"
    assert "1 route(s) down: Embed" in rows["health"]["detail"]
    assert rows["chat"]["result"] == "ok"

    allowed = smoke(stack.router.base_url, stack.key.token, "--json", "--allow-down")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert rows_of(allowed)["health"]["result"] == "ok"


def test_an_unreachable_router_fails_fast(factory):
    done = smoke(f"http://{HOST}:9", "sk-mo-anything", "--json")

    rows = rows_of(done)
    assert done.returncode == 1
    assert list(rows) == ["health"]
    assert rows["health"]["result"] == "FAIL"


@pytest.mark.parametrize(
    ("base_url", "key", "word"),
    [(None, "sk-mo-anything", "SMOKE_BASE_URL"), (f"http://{HOST}:9", None, "SMOKE_API_KEY")],
    ids=["no-url", "no-key"],
)
def test_it_does_not_start_without_url_or_key(base_url, key, word):
    done = smoke(base_url, key)

    assert done.returncode == 2
    assert word in done.stderr
    assert "Traceback" not in done.stderr


# ---- TLS ------------------------------------------------------------------------------


@pytest.fixture
def https(stack, factory, tmp_path):
    """The shared router behind a TLS front with a certificate no client trusts. Yields (URL, cert)."""
    if shutil.which("openssl") is None:
        pytest.skip("needs the openssl command to make a certificate")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    made = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=smoke test",
            "-addext", f"subjectAltName=IP:{HOST}",
            "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
        ],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    front = factory.relay(HOST, stack.router.port, tls=context)
    return f"https://{HOST}:{front.port}", cert


def test_an_untrusted_certificate_fails_with_a_hint(stack, world, https):
    url, _ = https

    done = smoke(url, stack.key.token, "--json")

    rows = rows_of(done)
    assert done.returncode == 1
    assert list(rows) == ["health"]
    assert "CERTIFICATE_VERIFY_FAILED" in rows["health"]["detail"]
    assert "--ca-file" in rows["health"]["detail"] and "--insecure" in rows["health"]["detail"]


def test_insecure_skips_verification(stack, world, https):
    url, _ = https

    done = smoke(url, stack.key.token, "--insecure")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "certificate NOT verified" in done.stdout.splitlines()[0]
    assert done.stdout.splitlines()[-1] == "12 passed, 0 failed, 0 skipped"


def test_ca_file_verifies_against_a_private_ca(stack, world, https):
    url, cert = https

    done = smoke(url, stack.key.token, "--json", "--ca-file", str(cert))

    assert done.returncode == 0, done.stdout + done.stderr
    assert {row["result"] for row in rows_of(done).values()} == {"ok"}


def test_an_unreadable_ca_file_is_a_usage_error(stack, tmp_path):
    done = smoke(stack.router.base_url, stack.key.token, "--ca-file", str(tmp_path / "missing.pem"))

    assert done.returncode == 2
    assert "--ca-file" in done.stderr
    assert "Traceback" not in done.stderr
