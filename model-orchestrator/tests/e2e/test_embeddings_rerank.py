"""Section 5: ``/v1/embeddings`` and ``/v1/rerank``. C5: rerank ``top_n``."""

from __future__ import annotations

import pytest

from tests.fakes import FakeReranker, FakeTEI
from tests.harness import world as w

EMBED = "/v1/embeddings"
RERANK = "/v1/rerank"
DOCS = ["alpha doc", "beta doc", "gamma doc", "delta doc"]


def test_embeddings_single_input(stack, world):
    r = stack.post(EMBED, json={"model": w.EMBED_ALIAS, "input": "one text"})

    assert r.status_code == 200, r.text
    seen = world.embed.last(EMBED)
    assert seen.json == {"model": w.EMBED_BACKEND_MODEL, "input": "one text"}
    assert r.content == seen.sent
    body = r.json()
    assert body["data"][0]["embedding"] == FakeTEI.vector("one text")
    assert body["usage"] == {"prompt_tokens": 3, "total_tokens": 3}


def test_embeddings_batch_and_unknown_fields(stack, world):
    sent = {
        "model": w.EMBED_ALIAS,
        "input": ["a b", "c", "ünïcode ✓"],
        "encoding_format": "float",
        "dimensions": 4,
        "truncate": True,
        "x_unknown": {"keep": ["me"]},
    }

    r = stack.post(EMBED, json=sent)

    assert r.status_code == 200, r.text
    assert world.embed.last(EMBED).json == {**sent, "model": w.EMBED_BACKEND_MODEL}
    assert [d["index"] for d in r.json()["data"]] == [0, 1, 2]


def test_embeddings_at_the_backend_batch_limit(stack, world):
    r = stack.post(EMBED, json={"model": w.EMBED_ALIAS, "input": ["x"] * FakeTEI.max_batch})

    assert r.status_code == 200
    assert len(r.json()["data"]) == FakeTEI.max_batch


def test_embeddings_backend_validation_error_keeps_its_status(stack, world):
    """The backend's 4xx status reaches the client. (How the message is wrapped is C4.)"""
    r = stack.post(EMBED, json={"model": w.EMBED_ALIAS, "input": ["x"] * 65})

    assert r.status_code == 422
    assert "batch size 65 > maximum allowed batch size 64" in r.json()["error"]["message"]
    assert len(world.embed.calls(EMBED)) == 1  # a 4xx is not retried


def test_rerank_round_trip(stack, world):
    sent = {
        "model": w.RERANK_ALIAS,
        "query": "which doc?",
        "documents": DOCS,
        "return_documents": True,
        "x_unknown": 1,
    }

    r = stack.post(RERANK, json=sent)

    assert r.status_code == 200, r.text
    seen = world.rerank.last(RERANK)
    assert seen.json == {**sent, "model": w.RERANK_BACKEND_MODEL}
    assert r.content == seen.sent
    body = r.json()
    assert len(body["results"]) == len(DOCS)
    scores = [item["relevance_score"] for item in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert body["usage"] == {"total_tokens": FakeReranker.tokens("which doc?", DOCS)}


def test_rerank_on_the_backends_own_model_id(stack, world):
    r = stack.post(RERANK, json={"model": w.RERANK_BACKEND_MODEL, "query": "q", "documents": DOCS})

    assert r.status_code == 200, r.text
    assert world.rerank.last(RERANK).json["model"] == w.RERANK_BACKEND_MODEL


def test_rerank_null_top_n_is_not_forwarded(stack, world):
    """``top_n: null`` crashes some backends (the fake answers 500 to it), so it is stripped."""
    r = stack.post(RERANK, json={"model": w.RERANK_ALIAS, "query": "q", "documents": DOCS, "top_n": None})

    assert r.status_code == 200, r.text
    assert "top_n" not in world.rerank.last(RERANK).json
    assert len(r.json()["results"]) == len(DOCS)


@pytest.mark.change("C5")
@pytest.mark.parametrize("top_n", [1, 2])
def test_rerank_forwards_a_numeric_top_n(stack, world, top_n):
    r = stack.post(RERANK, json={"model": w.RERANK_ALIAS, "query": "q", "documents": DOCS, "top_n": top_n})

    assert r.status_code == 200, r.text
    assert world.rerank.last(RERANK).json.get("top_n") == top_n
    assert len(r.json()["results"]) == top_n


@pytest.mark.legacy_only
def test_rerank_always_drops_top_n_today(stack, world):
    r = stack.post(RERANK, json={"model": w.RERANK_ALIAS, "query": "q", "documents": DOCS, "top_n": 2})

    assert r.status_code == 200
    assert "top_n" not in world.rerank.last(RERANK).json
    assert len(r.json()["results"]) == len(DOCS)
