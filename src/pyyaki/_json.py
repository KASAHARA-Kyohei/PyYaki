"""Strict JSON boundaries shared by the dispatcher and transport."""

import json
import math

from .errors import FrameworkError

MAX_MESSAGE_BYTES = 1_048_576
MAX_SAFE_INTEGER = 2**53 - 1


def check_json(value: object, code: str = "SERIALIZATION_ERROR", depth: int = 0) -> None:
    # A depth limit also bounds work on adversarial, deeply nested messages.
    if depth > 64:
        raise FrameworkError(code, "JSON nesting exceeds the prototype limit of 64")
    kind = type(value)
    if value is None or kind in (str, bool):
        return
    if kind is int and abs(value) <= MAX_SAFE_INTEGER:
        return
    if kind is float and math.isfinite(value):
        return
    if kind is list:
        for item in value:
            check_json(item, code, depth + 1)
        return
    if kind is dict and all(type(key) is str for key in value):
        for item in value.values():
            check_json(item, code, depth + 1)
        return
    raise FrameworkError(code, "Value is outside the supported JSON types or numeric range")


def encode(value: object) -> str:
    check_json(value)
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise FrameworkError("SERIALIZATION_ERROR", "Message exceeds 1 MiB")
        return text
    except UnicodeError as exc:
        raise FrameworkError("SERIALIZATION_ERROR", "Invalid Unicode string") from exc


def decode(text: str) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value

    try:
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("Message too large")
        value = json.loads(text, object_pairs_hook=pairs)
        check_json(value, "BAD_REQUEST")
        if type(value) is not dict:
            raise ValueError("Expected an object")
        return value
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise FrameworkError("BAD_REQUEST", "Invalid JSON message") from exc
