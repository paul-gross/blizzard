"""The JSON value type every harness payload carries, and the one decode that produces it."""

from __future__ import annotations

import json
from typing import cast

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def decode_json(text: str | bytes) -> JsonValue:
    """Decode ``text`` as JSON; raises ``json.JSONDecodeError`` on invalid input."""
    return cast(JsonValue, json.loads(text))
