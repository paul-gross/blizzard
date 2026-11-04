"""Helpers for the transcript pump tests, shared with other test modules."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from blizzard.runner.harness.transcript import (
    LateToolOutput,
    NormalizedTurn,
    SidechainConversation,
    TranscriptBatch,
    TranscriptPosition,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def normalized_turn(index: int, text: str) -> NormalizedTurn:
    return NormalizedTurn(
        index=index,
        kind="asst",
        timestamp=_NOW,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def transcript_batch(
    turns: list[NormalizedTurn],
    *,
    next_token: str,
    unlinked_sidechains: list[SidechainConversation] | None = None,
    truncated: bool = False,
    sidechain_truncated: bool = False,
    late_tool_outputs: list[LateToolOutput] | None = None,
    agent_tool_use_ids: dict[str, str] | None = None,
) -> TranscriptBatch:
    return TranscriptBatch(
        session_id="sess-a",
        available=True,
        reason=None,
        turns=turns,
        unlinked_sidechains=unlinked_sidechains or [],
        next_position=TranscriptPosition(next_token),
        complete=True,
        truncated=truncated,
        sidechain_truncated=sidechain_truncated,
        normalizer_version="fake/1",
        harness_version=None,
        late_tool_outputs=late_tool_outputs or [],
        agent_tool_use_ids=agent_tool_use_ids or {},
    )


def incomplete_batch(
    turns: list[NormalizedTurn], *, next_token: str, unlinked_sidechains: list[SidechainConversation] | None = None
) -> TranscriptBatch:
    """``_batch``'s ``complete=False`` counterpart — the per-batch read budget ran out
    before this whole window was covered, so ``next_position`` is where a caller resumes
    RIGHT NOW, not next tick."""
    return replace(
        transcript_batch(turns, next_token=next_token, unlinked_sidechains=unlinked_sidechains), complete=False
    )
