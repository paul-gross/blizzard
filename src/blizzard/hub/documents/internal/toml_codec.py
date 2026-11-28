"""TOML binding for JSON-compatible, TOML-representable documents."""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping
from typing import cast

import tomli_w

from blizzard.hub.documents.errors import ConfigDecodeError, ConfigEncodeError


def _check_value(value: object) -> None:
    if type(value) is dict:
        for key, item in cast("dict[object, object]", value).items():
            if type(key) is not str:
                raise ValueError("TOML keys must be strings")
            _check_value(item)
    elif type(value) is list:
        for item in cast("list[object]", value):
            _check_value(item)
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite floats are not supported")
    elif type(value) not in (str, int, bool):
        raise ValueError(f"unsupported TOML value: {type(value).__name__}")


class TomlConfigCodec:
    media_types = ("application/toml",)
    extensions = (".toml",)

    def decode(self, data: bytes) -> dict[str, object]:
        try:
            text = data.decode("utf-8")
            document = tomllib.loads(text)
            _check_value(document)
        except tomllib.TOMLDecodeError as exc:
            # Python 3.12's TOMLDecodeError exposes positions in its message only.
            match = re.search(r" \(at line (\d+), column (\d+)\)$", str(exc))
            if match:
                raise ConfigDecodeError(str(exc)[: match.start()], line=int(match[1]), column=int(match[2])) from exc
            raise ConfigDecodeError(str(exc)) from exc
        except (UnicodeError, ValueError) as exc:
            raise ConfigDecodeError(str(exc)) from exc
        return document

    def encode(self, document: Mapping[str, object]) -> bytes:
        try:
            value = dict(document)
            _check_value(value)
            return tomli_w.dumps(value).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise ConfigEncodeError(str(exc)) from exc


TOML_CODEC = TomlConfigCodec()


def decode_toml_value(text: str) -> object:
    """``text`` read as one TOML value, left unchecked: the caller judges what it can carry and words that refusal."""
    try:
        return tomllib.loads(f"value = {text}")["value"]
    except tomllib.TOMLDecodeError as exc:
        raise ConfigDecodeError(str(exc)) from exc
