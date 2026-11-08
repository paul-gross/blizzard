"""The transcript text cap and the compaction-window parse every harness binding shares."""

from __future__ import annotations

import pytest

from blizzard.runner.harness import harness_shared
from blizzard.runner.harness.harness_shared import (
    CompactionWindow,
    InvalidCompactionWindow,
    cap_block,
    parse_compaction_window,
)
from blizzard.runner.harness.opencode.transcript.normalizer import Text as OpenCodeText


def test_the_cap_is_one_mebi_characters() -> None:
    assert harness_shared.MAX_BLOCK_CHARS == 1_048_576


@pytest.mark.parametrize(("delta", "cut"), [(-1, False), (0, False), (1, True)])
def test_the_cap_cuts_only_above_the_limit(delta: int, cut: bool) -> None:
    raw = "x" * (harness_shared.MAX_BLOCK_CHARS + delta)
    text, truncated = cap_block(raw)
    assert truncated is cut
    assert len(text) == min(len(raw), harness_shared.MAX_BLOCK_CHARS)


def test_the_cap_counts_characters_not_bytes() -> None:
    raw = "é" * (harness_shared.MAX_BLOCK_CHARS // 2 + 1)
    assert len(raw.encode()) > harness_shared.MAX_BLOCK_CHARS > len(raw)
    assert cap_block(raw) == (raw, False)


def test_the_cap_is_read_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness_shared, "MAX_BLOCK_CHARS", 3)
    assert cap_block("abcde") == ("abc", True)


def test_an_opencode_block_keeps_ansi_verbatim() -> None:
    assert OpenCodeText.of("\x1b[31mred\x1b[0m") == OpenCodeText("\x1b[31mred\x1b[0m", False)


def test_no_window_is_none() -> None:
    assert parse_compaction_window(None) is None


@pytest.mark.parametrize(
    ("spelling", "tokens"),
    [("auto", None), ("0", 0), ("8k", 8000), ("8K", 8000), ("400000", 400_000), ("007", 7)],
)
def test_a_well_formed_window_keeps_its_spelling_and_token_count(spelling: str, tokens: int | None) -> None:
    parsed = parse_compaction_window(spelling)
    assert parsed == CompactionWindow(spelling, tokens)


@pytest.mark.parametrize(
    "spelling", ["", "AUTO", " 8k", "8k ", "8 k", "150k tokens", "٣", "\uff18k", "-1", "8kk", "auto2"]
)
def test_an_ill_formed_window_is_an_invalid_result_not_an_exception(spelling: str) -> None:
    assert parse_compaction_window(spelling) == InvalidCompactionWindow(spelling)
