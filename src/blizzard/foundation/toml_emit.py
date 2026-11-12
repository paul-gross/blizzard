"""TOML emission helpers shared by every config file a daemon writes: a scalar literal, a JSON-encoded value literal
and the line that is commented out at its default and live once overridden."""

from __future__ import annotations

import json
from typing import cast


def toml_literal(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def json_literal(value: object) -> str:
    """``value`` as a TOML-valid literal: JSON-encoded, a tuple as an array."""
    return json.dumps(list(cast("tuple[object, ...]", value)) if isinstance(value, tuple) else value)


def default_line(key: str, literal: str, *, at_default: bool) -> str:
    """One ``key = literal`` line: commented out while ``at_default``, live once overridden."""
    return f"# {key} = {literal}\n" if at_default else f"{key} = {literal}\n"
