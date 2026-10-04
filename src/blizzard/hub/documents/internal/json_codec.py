"""The JSON binding of the config codec. JSON is a subset of YAML 1.2, so decode is the strict YAML decode —
one set of errors and one scalar model for every format."""

from __future__ import annotations

import json
from collections.abc import Mapping

from blizzard.hub.documents.internal.yaml_codec import YAML_CODEC


class JsonCodec:
    media_types = ("application/json",)
    extensions = (".json",)

    def decode(self, data: bytes) -> dict[str, object]:
        return YAML_CODEC.decode(data)

    def encode(self, document: Mapping[str, object]) -> bytes:
        return json.dumps(dict(document), ensure_ascii=False, indent=2).encode("utf-8")


JSON_CODEC = JsonCodec()
