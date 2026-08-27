"""The prompt-tree byte bars the prompt-authoring README legislates, asserted rather than
printed: every packaged node prompt within 4,000 bytes, `blizzard_preamble.md` — frontloaded
into every worker session in every deployment — within 2,252."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.graphs import PACKAGED

NODE_PROMPT_BAR = 4_000
PREAMBLE_BAR = 2_252

_RUNNER_PROMPTS = Path(__file__).resolve().parents[1] / "src" / "blizzard" / "runner" / "harness" / "prompts"


def _over(files: list[Path], bar: int) -> list[str]:
    return [f"{path} ({path.stat().st_size} > {bar})" for path in files if path.stat().st_size > bar]


@pytest.mark.unit
def test_every_packaged_graph_prompt_stays_within_the_byte_bar() -> None:
    files = sorted(PACKAGED.root.glob("*/prompts/*.md"))
    assert files, "no packaged graph prompts found — the glob root moved"
    assert _over(files, NODE_PROMPT_BAR) == []


@pytest.mark.unit
def test_the_runner_prompt_tree_inherits_the_same_bar() -> None:
    files = sorted(_RUNNER_PROMPTS.glob("*.md"))
    assert files, "no runner prompt files found — the tree moved"
    assert _over(files, NODE_PROMPT_BAR) == []


@pytest.mark.unit
def test_the_blizzard_preamble_stays_within_its_own_tighter_bar() -> None:
    assert _over([_RUNNER_PROMPTS / "blizzard_preamble.md"], PREAMBLE_BAR) == []
