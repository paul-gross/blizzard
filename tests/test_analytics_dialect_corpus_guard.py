"""D5's registration guard: every tool name a dialect registers for a kind must
actually occur in that dialect's own pinned compatibility corpus — the one test
in the analytics-extraction lane that reads
``src/blizzard/runner/harness/contracts/opencode/<version>/`` directly, where the corpus
files are the subject, not an in-file builder (blizzard#439)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.domain.analytics.dialects import DIALECTS
from blizzard.runner.harness.internal.claude_code_normalizer import NORMALIZER_VERSION as _CLAUDE_CODE_VERSION
from blizzard.runner.harness.internal.opencode_normalizer import NORMALIZER_VERSION as _OPENCODE_VERSION

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "harness"

#: Every dialect with a pinned compatibility corpus — Claude Code has none (D5).
_CORPUS_DIRS: dict[str, Path] = {
    "opencode-export/1": _PACKAGE_ROOT / "contracts" / "opencode" / "1.18.25",
}


def _tool_inputs_in_corpus(corpus_dir: Path) -> dict[str, set[str]]:
    """Every ``tool``'s own ``state.input`` key set actually observed in the corpus —
    keyed by tool name, so both the tool name and its argument key are checkable."""
    inputs: dict[str, set[str]] = {}

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            tool = node.get("tool")
            if isinstance(tool, str):
                state = node.get("state")
                keys = (
                    set(state["input"]) if isinstance(state, dict) and isinstance(state.get("input"), dict) else set()
                )
                inputs.setdefault(tool, set()).update(keys)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for path in corpus_dir.glob("*.json"):
        _walk(json.loads(path.read_text()))
    return inputs


def test_every_corpus_backed_dialect_is_registered() -> None:
    """A pinned corpus with no registry entry at all would go unchecked below —
    assert every corpus-backed dialect this guard knows about is actually registered."""
    assert set(_CORPUS_DIRS) <= set(DIALECTS)


def test_every_runner_normalizer_version_is_registered() -> None:
    """The hub's `DIALECTS` keys are string literals, deliberately not imported from
    `blizzard.runner` (D1) — this test is the one place that still ties them to the
    runner's own normalizer stamps, so the two can drift apart only silently past here."""
    assert {_CLAUDE_CODE_VERSION, _OPENCODE_VERSION} <= set(DIALECTS)


@pytest.mark.parametrize("normalizer_version", sorted(_CORPUS_DIRS))
def test_every_registered_entry_matches_its_pinned_corpus(normalizer_version: str) -> None:
    corpus_inputs = _tool_inputs_in_corpus(_CORPUS_DIRS[normalizer_version])
    registered = DIALECTS[normalizer_version]

    for kind, entry in registered.items():
        assert entry.tool_name in corpus_inputs, (
            f"{normalizer_version}'s {kind} entry registers tool {entry.tool_name!r}, "
            f"not present in {_CORPUS_DIRS[normalizer_version]}"
        )
        assert entry.argument_key in corpus_inputs[entry.tool_name], (
            f"{normalizer_version}'s {kind} entry registers argument key {entry.argument_key!r} for tool "
            f"{entry.tool_name!r}, not among the keys {corpus_inputs[entry.tool_name]!r} that tool's own pinned "
            f"corpus calls actually carry"
        )
