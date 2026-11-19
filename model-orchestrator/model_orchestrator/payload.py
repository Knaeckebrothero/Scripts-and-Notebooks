"""The few things the router does to a request body.

The router forwards bodies; it does not reshape them. Apart from the ``model``
rewrite for aliases, these are all the changes it ever makes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def apply_request_defaults(payload: dict[str, Any], defaults: Mapping[str, Any]) -> None:
    """Merge a route's ``request_defaults`` into the outbound payload, in place.

    A value the client sent always wins. Dict-valued defaults (for example
    ``chat_template_kwargs``) are shallow-merged key by key, so a route can add
    a key without dropping the ones the client supplied. A client value of the
    wrong shape (not a dict where the default is one) is left untouched.
    """
    for key, default in defaults.items():
        if isinstance(default, dict):
            client_value = payload.get(key)
            if isinstance(client_value, dict):
                payload[key] = {**default, **client_value}
            elif key not in payload:
                payload[key] = dict(default)
        else:
            payload.setdefault(key, default)


def drop_null_top_n(payload: dict[str, Any]) -> None:
    """Rerank: ``top_n: null`` crashes some backends. A number is forwarded."""
    if "top_n" in payload and payload["top_n"] is None:
        del payload["top_n"]


def inject_stream_usage(payload: dict[str, Any]) -> bool:
    """Ask the backend for the usage event of a streamed chat completion.

    Returns True when the router added the option itself. The usage-only event
    must then be kept from the client, who did not ask for it. If the client did
    ask, nothing is changed and the stream is relayed untouched.
    """
    options = payload.get("stream_options")
    if options is None:
        payload["stream_options"] = {"include_usage": True}
        return True
    if not isinstance(options, dict):
        # Not ours to fix: the backend rejects it, exactly as without the router.
        return False
    if options.get("include_usage"):
        return False
    payload["stream_options"] = {**options, "include_usage": True}
    return True


def encode_json(payload: Any) -> bytes:
    """Serialise a payload for the backend, compact and in UTF-8."""
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate (from a \\udXXX escape in the client's JSON) cannot be
        # written as UTF-8; escaped output passes it on the way it came.
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
