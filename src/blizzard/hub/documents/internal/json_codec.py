"""The JSON binding of the config codec. Decoding is ``json``'s own, so every valid JSON document reads — tab
whitespace and surrogate-pair escapes included — with a duplicate key refused as the YAML binding refuses it."""

from __future__ import annotations

import json
from collections.abc import Mapping

from blizzard.hub.documents.errors import ConfigDecodeError


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate key {key!r}")
        document[key] = value
    return document


class JsonCodec:
    media_types = ("application/json",)
    extensions = (".json",)

    def decode(self, data: bytes) -> dict[str, object]:
        try:
            loaded = json.loads(data, object_pairs_hook=_no_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ConfigDecodeError(exc.msg, line=exc.lineno, column=exc.colno) from exc
        except ValueError as exc:
            raise ConfigDecodeError(str(exc)) from exc
        if not isinstance(loaded, dict):
            raise ConfigDecodeError("document root must be a mapping")
        return loaded

    def encode(self, document: Mapping[str, object]) -> bytes:
        return json.dumps(dict(document), ensure_ascii=False, indent=2).encode("utf-8")


JSON_CODEC = JsonCodec()
