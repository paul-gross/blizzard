"""TOML emission helpers shared by every config file a daemon writes and every settings document a daemon's CLI
prints: a scalar literal, the line that is commented out at its default and live once overridden, and a whole
document's tables."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast


def toml_literal(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def default_line(key: str, literal: str, *, at_default: bool) -> str:
    """One ``key = literal`` line: commented out while ``at_default``, live once overridden."""
    return f"# {key} = {literal}\n" if at_default else f"{key} = {literal}\n"


def toml_document(document: Mapping[str, object]) -> str:
    """``document`` as TOML text that decodes back to it: each table as a ``[name]`` section, each non-empty list of
    tables as ``[[name]]`` sections with their own sub-tables under them, every other value inline."""
    return "\n".join(_table(document, ())).lstrip() + "\n"


def _key(part: str) -> str:
    return json.dumps(part, ensure_ascii=False)


def _is_tables(value: object) -> bool:
    if not isinstance(value, list | tuple):
        return False
    items = cast(Sequence[object], value)
    return bool(items) and all(isinstance(item, dict) for item in items)


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        items = cast(Mapping[str, object], value).items()
        return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in items) + " }" if items else "{}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_value(item) for item in cast(Sequence[object], value)) + "]"
    return json.dumps(value, ensure_ascii=False)


def _table(table: Mapping[str, object], prefix: tuple[str, ...]) -> list[str]:
    lines = [
        f"{_key(key)} = {_value(value)}"
        for key, value in table.items()
        if not isinstance(value, Mapping) and not _is_tables(value)
    ]
    for key, value in table.items():
        path = (*prefix, key)
        header = ".".join(_key(part) for part in path)
        if isinstance(value, Mapping):
            lines.extend(["", f"[{header}]", *_table(cast(Mapping[str, object], value), path)])
        elif _is_tables(value):
            for item in cast(Sequence[Mapping[str, object]], value):
                lines.extend(["", f"[[{header}]]", *_table(item, path)])
    return lines
