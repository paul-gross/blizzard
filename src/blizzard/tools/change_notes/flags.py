"""Read flag registry data for change notes without importing runtime flag code."""

from __future__ import annotations

import tomllib
from pathlib import Path

from blizzard.tools.change_notes.contract import FLAG_REGISTRY
from blizzard.tools.compat.git import Repository


def registry(text: str) -> dict[str, dict[str, str]]:
    """Live flag declarations in registry TOML (retired names are excluded)."""
    return tomllib.loads(text).get("flag", {})


def working_registry(root: Path) -> dict[str, dict[str, str]]:
    path = root / FLAG_REGISTRY
    return registry(path.read_text(encoding="utf-8")) if path.is_file() else {}


def turned_on(repo: Repository, tag: str) -> list[str]:
    """Summaries newly on at this release, ordered by flag name."""
    current = repo.show(tag, FLAG_REGISTRY)
    if current is None:
        return []
    previous = repo.previous_tag(tag)
    previous_text = repo.show(previous, FLAG_REGISTRY) if previous else None
    before = registry(previous_text) if previous_text is not None else {}
    after = registry(current)
    return [
        fields["summary"]
        for name, fields in sorted(after.items())
        if fields["default"] == "on" and before.get(name, {}).get("default") != "on"
    ]
