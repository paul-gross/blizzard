"""This registration guard: every tool name a dialect registers for a kind must
actually occur in a live capture of that dialect's harness — the one test
in the analytics-extraction lane that reads
``src/blizzard/runner/harness/contracts/opencode/<version>/`` directly, where the corpus
files are the subject, not an in-file builder. A fixture its version's manifest marks
``source: synthetic`` is hand-authored, so it proves nothing and is excluded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.domain.analytics.dialects import DIALECTS
from blizzard.runner.harness.catalog import declared_normalizer_versions
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_PACKAGE_ROOT = repo_root() / "src" / "blizzard" / "runner" / "harness"

#: Every dialect with pinned captures — Claude Code has none. A dialect's captures are the
#: admitted corpus plus every live supplement, one directory per recorded version.
_CORPUS_DIRS: dict[str, Path] = {
    "opencode-export/1": _PACKAGE_ROOT / "contracts" / "opencode",
}


def _synthetic_fixtures(version_dir: Path) -> set[str]:
    """File names its version's manifest marks ``source: synthetic``; none without a manifest."""
    manifest = version_dir / "manifest.json"
    if not manifest.is_file():
        return set()
    fixtures = json.loads(manifest.read_text()).get("fixtures", [])
    return {
        str(Path(fixture["path"]).name)
        for fixture in fixtures
        if isinstance(fixture, dict) and fixture.get("source") == "synthetic" and "path" in fixture
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

    for version_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
        synthetic = _synthetic_fixtures(version_dir)
        for path in version_dir.glob("*.json"):
            if path.name != "manifest.json" and path.name not in synthetic:
                _walk(json.loads(path.read_text()))
    return inputs


def test_every_corpus_backed_dialect_is_registered() -> None:
    """A pinned corpus with no registry entry at all would go unchecked below —
    assert every corpus-backed dialect this guard knows about is actually registered."""
    assert set(_CORPUS_DIRS) <= set(DIALECTS)


@pytest.mark.parametrize("normalizer_version", declared_normalizer_versions())
def test_every_runner_normalizer_version_is_registered(normalizer_version: str) -> None:
    """The hub's `DIALECTS` keys are string literals, deliberately not imported from
    `blizzard.runner` — this test is the one place that ties them to every normalizer
    stamp the runner's harness catalog declares, so a newly declared harness with no
    dialect entry fails here rather than drifting silently."""
    assert normalizer_version in DIALECTS, (
        f"the harness catalog declares normalizer version {normalizer_version!r}, which has no DIALECTS entry"
    )


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
