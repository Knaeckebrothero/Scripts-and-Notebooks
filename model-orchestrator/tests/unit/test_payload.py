"""What the router does to a request body: defaults merging, rerank, stream usage."""

from __future__ import annotations

import json
from typing import Any

import pytest

from model_orchestrator.payload import (
    apply_request_defaults,
    drop_null_top_n,
    encode_json,
    inject_stream_usage,
)

GEMMA_DEFAULTS = {"skip_special_tokens": False, "chat_template_kwargs": {"enable_thinking": True}}


def merged(payload: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    apply_request_defaults(payload, defaults)
    return payload


def test_defaults_fill_what_the_client_left_out() -> None:
    assert merged({"model": "m"}, GEMMA_DEFAULTS) == {
        "model": "m",
        "skip_special_tokens": False,
        "chat_template_kwargs": {"enable_thinking": True},
    }


def test_the_clients_scalar_always_wins() -> None:
    assert merged({"skip_special_tokens": True}, GEMMA_DEFAULTS)["skip_special_tokens"] is True
    assert merged({"temperature": 0}, {"temperature": 0.7})["temperature"] == 0
    # An explicit null is a value the client sent.
    assert merged({"temperature": None}, {"temperature": 0.7})["temperature"] is None


def test_dicts_are_shallow_merged_key_by_key() -> None:
    payload = {"chat_template_kwargs": {"reasoning_strength": "low"}}
    assert merged(payload, GEMMA_DEFAULTS)["chat_template_kwargs"] == {
        "enable_thinking": True,
        "reasoning_strength": "low",
    }


def test_the_client_wins_on_a_key_collision_inside_a_dict() -> None:
    payload = {"chat_template_kwargs": {"enable_thinking": False}}
    assert merged(payload, GEMMA_DEFAULTS)["chat_template_kwargs"] == {"enable_thinking": False}


def test_the_merge_is_shallow_not_deep() -> None:
    defaults = {"extra": {"nested": {"a": 1, "b": 2}}}
    assert merged({"extra": {"nested": {"a": 9}}}, defaults)["extra"] == {"nested": {"a": 9}}


@pytest.mark.parametrize("client_value", [None, "off", 3, ["x"]])
def test_a_client_value_of_the_wrong_shape_is_left_alone(client_value: Any) -> None:
    payload = {"chat_template_kwargs": client_value}
    assert merged(payload, GEMMA_DEFAULTS)["chat_template_kwargs"] == client_value


def test_defaults_are_copied_not_shared() -> None:
    defaults = {"chat_template_kwargs": {"enable_thinking": True}}
    first = merged({}, defaults)
    first["chat_template_kwargs"]["enable_thinking"] = False
    assert defaults == {"chat_template_kwargs": {"enable_thinking": True}}
    assert merged({}, defaults)["chat_template_kwargs"] == {"enable_thinking": True}


def test_no_defaults_is_a_no_op() -> None:
    assert merged({"model": "m", "x": 1}, {}) == {"model": "m", "x": 1}


# -- rerank (C5) -------------------------------------------------------------------


@pytest.mark.parametrize("top_n", [3, 0, 2.0, "3"])
def test_top_n_is_forwarded_when_it_has_a_value(top_n: Any) -> None:
    payload = {"query": "q", "top_n": top_n}
    drop_null_top_n(payload)
    assert payload == {"query": "q", "top_n": top_n}


def test_top_n_null_is_dropped_and_absent_stays_absent() -> None:
    payload: dict[str, Any] = {"query": "q", "top_n": None}
    drop_null_top_n(payload)
    assert payload == {"query": "q"}
    drop_null_top_n(payload)
    assert payload == {"query": "q"}


# -- stream usage (design 7.6) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({}, {"stream_options": {"include_usage": True}}),
        ({"stream_options": None}, {"stream_options": {"include_usage": True}}),
        ({"stream_options": {}}, {"stream_options": {"include_usage": True}}),
        ({"stream_options": {"include_usage": False}}, {"stream_options": {"include_usage": True}}),
        (
            {"stream_options": {"continuous_usage_stats": False}},
            {"stream_options": {"continuous_usage_stats": False, "include_usage": True}},
        ),
    ],
)
def test_usage_is_requested_when_the_client_did_not(before: dict[str, Any], after: dict[str, Any]) -> None:
    payload = {"stream": True, **before}
    assert inject_stream_usage(payload) is True
    assert payload == {"stream": True, **after}


def test_the_clients_own_options_object_is_not_mutated() -> None:
    options = {"continuous_usage_stats": True}
    payload = {"stream_options": options}
    inject_stream_usage(payload)
    assert options == {"continuous_usage_stats": True}


def test_nothing_changes_when_the_client_asked_for_usage() -> None:
    payload = {"stream_options": {"include_usage": True, "continuous_usage_stats": True}}
    assert inject_stream_usage(payload) is False
    assert payload == {"stream_options": {"include_usage": True, "continuous_usage_stats": True}}


@pytest.mark.parametrize("bad", ["yes", 1, ["include_usage"]])
def test_a_malformed_stream_options_is_left_for_the_backend_to_reject(bad: Any) -> None:
    payload = {"stream_options": bad}
    assert inject_stream_usage(payload) is False
    assert payload == {"stream_options": bad}


# -- serialisation ------------------------------------------------------------------------


def test_encode_json_round_trips_and_keeps_unicode_and_order() -> None:
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Grüße 👋"}],
        "z": 1.5,
        "a": None,
        "n": 10**20,
    }
    raw = encode_json(payload)
    assert json.loads(raw) == payload
    assert list(json.loads(raw)) == ["model", "messages", "z", "a", "n"]
    assert "Grüße 👋".encode() in raw


def test_encode_json_survives_a_lone_surrogate() -> None:
    payload = json.loads('{"content": "broken \\ud83d emoji"}')
    raw = encode_json(payload)
    assert json.loads(raw) == payload
    assert b"\\ud83d" in raw
