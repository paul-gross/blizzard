"""D5's registration guard: every tool name a dialect registers for a kind must
actually occur in that dialect's own pinned compatibility corpus — the one test
in the analytics-extraction lane that reads ``contracts/opencode/<version>/``
directly, where the corpus files are the subject, not an in-file builder
(blizzard#439)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.domain.analytics.dialects import DIALECTS

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every dialect this build has a pinned compatibility corpus for — Claude Code
#: has no ``contracts/`` corpus, so OpenCode is the only one this guard checks (D5).
_CORPUS_DIRS: dict[str, Path] = {
    "opencode-export/1": _REPO_ROOT / "contracts" / "opencode" / "1.18.25",
}


def _tool_names_in_corpus(corpus_dir: Path) -> set[str]:
    names: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            tool = node.get("tool")
            if isinstance(tool, str):
                names.add(tool)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for path in corpus_dir.glob("*.json"):
        _walk(json.loads(path.read_text()))
    return names


def test_every_corpus_backed_dialect_is_registered() -> None:
    """A pinned corpus with no registry entry at all would go unchecked below —
    assert every corpus-backed dialect this guard knows about is actually registered."""
    assert set(_CORPUS_DIRS) <= set(DIALECTS)


@pytest.mark.parametrize("normalizer_version", sorted(_CORPUS_DIRS))
def test_every_registered_tool_name_occurs_in_its_pinned_corpus(normalizer_version: str) -> None:
    corpus_names = _tool_names_in_corpus(_CORPUS_DIRS[normalizer_version])
    registered = DIALECTS[normalizer_version]

    for kind, entry in registered.items():
        assert entry.tool_name in corpus_names, (
            f"{normalizer_version}'s {kind} entry registers tool {entry.tool_name!r}, "
            f"not present in {_CORPUS_DIRS[normalizer_version]}"
        )
