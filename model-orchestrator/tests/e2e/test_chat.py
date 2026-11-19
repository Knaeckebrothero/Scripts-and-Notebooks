"""Section 5: chat completions without streaming, on chat and vision routes.

Covers "bodies are forwarded untouched", unknown fields in both directions,
the ``reasoning`` field, and ``request_defaults``.
"""

from __future__ import annotations

import copy

import pytest

from tests.fakes import FakeVLLM
from tests.harness import world as w
from tests.harness.stack import user

CHAT = "/v1/chat/completions"

# A request full of things the router knows nothing about. All of it must arrive.
RICH_BODY = {
    "messages": [
        {"role": "system", "content": "You are terse. ✓"},
        {"role": "user", "content": "Grüße, 世界 — what is 2+2?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                                                              "function": {"name": "add", "arguments": "{\"a\":2,\"b\":2}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "4"},
    ],
    "temperature": 0.25,
    "top_p": 1,
    "max_tokens": 64,
    "seed": 12345678901234567890,
    "stop": ["\n\n", "END"],
    "logit_bias": {"50256": -100},
    "tools": [{"type": "function", "function": {"name": "add", "parameters": {"type": "object", "properties": {}}}}],
    "tool_choice": "auto",
    "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}},
    "chat_template_kwargs": {"enable_thinking": False},
    "guided_json": None,
    "x_unknown_scalar": "kept",
    "x_unknown_nested": {"a": [1, 2.5, None, True, {"b": "c"}], "empty": {}},
    "user": "someone",
}  # fmt: skip


def test_chat_completion_round_trip(stack, world):
    r = stack.chat(w.CHAT_ALIAS, "What is up?")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    seen = world.chat.last(CHAT)
    assert seen.method == "POST"
    assert seen.headers["content-type"].startswith("application/json")
    assert seen.json == {"model": w.CHAT_BACKEND_MODEL, "messages": user("What is up?")}
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "Echo: What is up? ✓ grüße"
    assert body["usage"] == {**FakeVLLM.usage(seen.json), "prompt_tokens_details": None}


def test_response_body_is_relayed_byte_for_byte(stack, world):
    """The router does not parse and re-serialise what the backend answered."""
    r = stack.chat(w.CHAT_ALIAS, "bytes please")

    seen = world.chat.last(CHAT)
    assert r.content == seen.sent
    assert r.content == FakeVLLM.render_completion(seen.json)
    assert b'"caf\\u00e9"' in r.content and "café ✓".encode() in r.content
    assert b"12345678901234567890123" in r.content and b"1.50" in r.content


def test_reasoning_field_reaches_the_client(stack, world):
    r = stack.chat(w.CHAT_ALIAS)

    message = r.json()["choices"][0]["message"]
    assert message["reasoning"] == "Let me think about it. 思考"
    assert "reasoning_content" not in message


def test_unknown_response_fields_reach_the_client(stack, world):
    body = stack.chat(w.CHAT_ALIAS).json()

    assert body["x_fake"]["nested"] == {"list": [1, None, True]}
    assert body["kv_transfer_params"] is None
    assert body["choices"][0]["stop_reason"] is None


def test_request_is_forwarded_untouched_apart_from_model(stack, world):
    sent = {"model": w.CHAT_ALIAS, **copy.deepcopy(RICH_BODY)}

    r = stack.post(CHAT, json=sent)

    assert r.status_code == 200, r.text
    assert world.chat.last(CHAT).json == {**sent, "model": w.CHAT_BACKEND_MODEL}


def test_every_alias_of_a_route_is_rewritten_to_the_backend_name(stack, world):
    for alias in (w.CHAT_ALIAS, w.CHAT_ALIAS_2):
        assert stack.chat(alias).status_code == 200
        assert world.chat.last(CHAT).json["model"] == w.CHAT_BACKEND_MODEL


@pytest.mark.parametrize("model", [w.CHAT_BACKEND_MODEL, w.CHAT_DISCOVERED])
def test_discovered_id_is_forwarded_as_sent(stack, world, model):
    r = stack.chat(model)

    assert r.status_code == 200, r.text
    assert world.chat.last(CHAT).json["model"] == model
    assert r.json()["model"] == model


def test_response_model_field_is_the_backends_not_the_alias(stack, world):
    assert stack.chat(w.CHAT_ALIAS).json()["model"] == w.CHAT_BACKEND_MODEL


def test_vision_route_serves_chat_completions(stack, world):
    content = [
        {"type": "text", "text": "What is in this image?"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==", "detail": "low"},
        },
    ]
    sent = {"model": w.VISION_ALIAS, "messages": [{"role": "user", "content": content}], "max_tokens": 5}

    r = stack.post(CHAT, json=sent)

    assert r.status_code == 200, r.text
    seen = world.vision.last(CHAT)
    assert seen.json == {**sent, "model": w.VISION_BACKEND_MODEL}
    assert r.content == seen.sent
    assert not world.chat.calls(CHAT)


def test_stream_false_is_a_plain_json_response(stack, world):
    r = stack.chat(w.CHAT_ALIAS, body={"stream": False})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert world.chat.last(CHAT).json["stream"] is False


# ---- request_defaults ------------------------------------------------------


def defaults_seen(stack, world, **body):
    r = stack.chat(w.REASONER_ALIAS, body=body)
    assert r.status_code == 200, r.text
    return world.reasoner.last(CHAT).json


def test_request_defaults_fill_in_missing_fields(stack, world):
    seen = defaults_seen(stack, world)

    assert seen == {"model": w.REASONER_BACKEND_MODEL, "messages": user("hello"), **w.REASONER_DEFAULTS}


def test_request_defaults_never_override_a_client_value(stack, world):
    seen = defaults_seen(stack, world, skip_special_tokens=True, temperature=0)

    assert seen["skip_special_tokens"] is True
    assert seen["temperature"] == 0
    assert seen["chat_template_kwargs"] == w.REASONER_DEFAULTS["chat_template_kwargs"]


def test_request_defaults_explicit_null_from_client_wins(stack, world):
    seen = defaults_seen(stack, world, temperature=None, chat_template_kwargs=None)

    assert "temperature" in seen and seen["temperature"] is None
    assert "chat_template_kwargs" in seen and seen["chat_template_kwargs"] is None


def test_request_defaults_dicts_merge_key_by_key(stack, world):
    seen = defaults_seen(stack, world, chat_template_kwargs={"reasoning_strength": "low", "custom": 1})

    # the client's key wins, its extra key is kept, the default's other key is added
    assert seen["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_strength": "low", "custom": 1}


def test_request_defaults_merge_is_shallow(stack, world):
    """Only the top level of a dict default is merged; nested values are taken whole."""
    seen = defaults_seen(stack, world, chat_template_kwargs={"enable_thinking": {"deep": True}})

    assert seen["chat_template_kwargs"] == {"enable_thinking": {"deep": True}, "reasoning_strength": "high"}


def test_request_defaults_leave_a_non_dict_client_value_alone(stack, world):
    seen = defaults_seen(stack, world, chat_template_kwargs="not-a-dict")

    assert seen["chat_template_kwargs"] == "not-a-dict"


def test_request_defaults_apply_to_streaming_requests(stack, world):
    s = stack.chat_stream(w.REASONER_ALIAS)

    assert s.status == 200
    seen = world.reasoner.last(CHAT).json
    for key, value in w.REASONER_DEFAULTS.items():
        assert seen[key] == value


def test_request_defaults_apply_to_a_discovered_id_of_the_route(stack, world):
    r = stack.chat(w.REASONER_BACKEND_MODEL)

    assert r.status_code == 200, r.text
    seen = world.reasoner.last(CHAT).json
    assert seen["model"] == w.REASONER_BACKEND_MODEL
    assert seen["skip_special_tokens"] is False


def test_routes_without_request_defaults_add_nothing(stack, world):
    stack.chat(w.CHAT_ALIAS)

    assert set(world.chat.last(CHAT).json) == {"model", "messages"}
