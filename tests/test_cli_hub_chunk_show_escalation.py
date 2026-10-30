"""``blizzard hub chunk get`` prints an open escalation's cause, detail, and takeover command."""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.hub.cli.chunk import ChunkDetail

pytestmark = pytest.mark.unit

_BODY: dict[str, Any] = {"chunk_id": "ch_1", "status": "needs_human", "graph_id": "gr_1", "node_name": "build"}


def _lines(escalation: dict[str, Any] | None) -> list[str]:
    return list(ChunkDetail(_BODY | {"escalation": escalation}).lines())


def test_an_escalated_chunk_prints_cause_detail_and_the_wrapped_command() -> None:
    lines = _lines(
        {
            "epoch": 2,
            "cause": "retries-exhausted",
            "detail": "build failed 3 times",
            "takeover_command": "cd /ws && claude --resume s",
            "wrapped_takeover_command": "blizzard runner takeover ch_1 --dir /r --runner r-claude",
        }
    )

    assert "  escalated: retries-exhausted" in lines
    assert "    detail: build failed 3 times" in lines
    assert "    takeover: blizzard runner takeover ch_1 --dir /r --runner r-claude" in lines
    assert not any("claude --resume" in line for line in lines)


def test_without_a_wrapped_form_the_raw_command_is_printed() -> None:
    lines = _lines({"epoch": 1, "takeover_command": "cd /ws && claude --resume s", "wrapped_takeover_command": ""})

    assert "  escalated: unknown cause" in lines
    assert "    takeover: cd /ws && claude --resume s" in lines


def test_a_chunk_without_an_escalation_prints_no_block() -> None:
    assert not any("escalated" in line for line in _lines(None))
