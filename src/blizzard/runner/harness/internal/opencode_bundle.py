"""OpenCode's slice of the operator harness-config bundle.

OpenCode resolves a ``{file:…}`` substitution relative to the config file's directory, so
``opencode.json`` declares an extractor for those references."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from blizzard.runner.harness.bundle import EntryPoint, HarnessLayout

_FILE_SUBSTITUTION = re.compile(r"\{file:([^}]+)\}")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def file_substitutions(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Every ``{file:<path>}`` reference in the document's string values."""
    return tuple(match.strip() for text in _strings(document) for match in _FILE_SUBSTITUTION.findall(text))


OPENCODE_BUNDLE_LAYOUT = HarnessLayout(
    dirname="opencode",
    entry_points=(
        EntryPoint("opencode.json", companions=file_substitutions),
        EntryPoint("plugins", is_dir=True),
    ),
)
