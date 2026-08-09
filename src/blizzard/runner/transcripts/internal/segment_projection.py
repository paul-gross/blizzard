"""Projects a hub segment's turns onto the panel's read model (blizzard#249, D5).

Narrows like the local path — drops ``thinking`` turns and sidechains wholesale, but
counts what it drops. Total rather than raising (review F1): a malformed turn
(``kind="tool"`` with no payload, an unparseable ``timestamp``) degrades in place
instead of reaching this 200-always route as an exception."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from blizzard.runner.harness.transcript import ToolCall, ToolInputShape
from blizzard.runner.transcripts.internal.projected_transcript_repository import SerializedInput
from blizzard.runner.transcripts.repository import Turn, TurnKind
from blizzard.wire.transcript_segment import TurnSegmentView


def select_turns(turns: list[TurnSegmentView]) -> tuple[list[TurnSegmentView], list[int]]:
    """The segment's turns narrowed to the panel's kept set, paired one-for-one with how
    many turns each survivor's own drop absorbed — thinking/malformed turns dropped just
    before it, plus its own sidechain's nested count. Per-kept rather than one aggregate
    (review F4), so a caller applying the recency cap after this call can total only the
    drops the surviving turns actually carry, not the whole pre-cap history."""
    kept: list[TurnSegmentView] = []
    dropped_before: list[int] = []
    pending = 0
    for turn in turns:
        if turn.kind == "thinking" or (turn.kind == "tool" and turn.tool is None):
            pending += 1
            continue
        own = _count(turn.sidechain.turns) if turn.sidechain is not None else 0
        kept.append(turn)
        dropped_before.append(pending + own)
        pending = 0
    if pending and kept:
        # Trailing drops after the last kept turn have nowhere else to land — attributed
        # to the last surviving turn, so they are not silently lost from the total.
        dropped_before[-1] += pending
    return kept, dropped_before


def _count(turns: list[TurnSegmentView]) -> int:
    total = 0
    for turn in turns:
        total += 1
        if turn.sidechain is not None:
            total += _count(turn.sidechain.turns)
    return total


def _parse_timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def to_turn(turn: TurnSegmentView, index: int) -> Turn:
    """One kept segment turn, projected onto the panel's read model at ``index``. A
    turn reaching here with ``kind="tool"`` always carries a payload — ``select_turns``
    already dropped the malformed case — and an unparseable ``timestamp`` degrades to
    ``None`` rather than raising."""
    timestamp = _parse_timestamp(turn.timestamp)
    if turn.kind == "tool" and turn.tool is not None:
        tool = ToolCall(
            name=turn.tool.name,
            input=turn.tool.input,
            input_unparsed=turn.tool.input_unparsed,
            input_shape=cast(ToolInputShape, turn.tool.input_shape),
            tool_use_id=turn.tool.tool_use_id,
            output=turn.tool.output,
            output_truncated=turn.tool.output_truncated,
        )
        serialized = SerializedInput.of(tool)
        return Turn(
            index=index,
            kind="tool",
            timestamp=timestamp,
            text="",
            tool_name=tool.name,
            tool_input=serialized.text,
            tool_output=tool.output,
            truncated=turn.truncated or tool.output_truncated or serialized.truncated,
        )
    # A payload-less "tool" turn degrades here too, as plain text, instead of raising;
    # "thinking" never reaches here — `select_turns` already filtered it.
    kind: TurnKind = "env" if turn.kind == "env" else "asst"
    return Turn(
        index=index,
        kind=kind,
        timestamp=timestamp,
        text=turn.text,
        tool_name=None,
        tool_input=None,
        tool_output=None,
        truncated=turn.truncated,
    )
