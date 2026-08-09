"""Projects a hub segment's turns onto the panel's read model (blizzard#249, D5).

A segment turn carries ``thinking`` and recursive sidechains that the panel's
:class:`~blizzard.runner.transcripts.repository.Turn` has no slot for — the same gap the
local path's own narrowing already lives with (``internal/projected_transcript_repository.py``).
This mirrors that narrowing exactly — drop ``thinking`` turns and every sidechain wholesale
— but, unlike the local path, **counts** what it drops: the dropped ``thinking`` turn
itself, plus every turn nested under a dropped sidechain, recursively, since the whole
subagent conversation goes with it. Tool-input re-materialization is shared with the local
path's own :class:`~blizzard.runner.transcripts.internal.projected_transcript_repository.SerializedInput`,
not duplicated."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from blizzard.runner.harness.transcript import ToolCall, ToolInputShape
from blizzard.runner.transcripts.internal.projected_transcript_repository import SerializedInput
from blizzard.runner.transcripts.repository import Turn, TurnKind
from blizzard.wire.transcript_segment import TurnSegmentView


def select_turns(turns: list[TurnSegmentView]) -> tuple[list[TurnSegmentView], int]:
    """The segment's turns, narrowed to the panel's kept set, paired with how many turns
    that narrowing dropped. Unindexed and uncapped — indexing and the recency cap are the
    caller's, applied the same way the local path applies them
    (``internal/projected_transcript_repository.py``)."""
    kept: list[TurnSegmentView] = []
    dropped = 0
    for turn in turns:
        if turn.kind == "thinking":
            dropped += 1
            continue
        kept.append(turn)
        if turn.sidechain is not None:
            dropped += _count(turn.sidechain.turns)
    return kept, dropped


def _count(turns: list[TurnSegmentView]) -> int:
    total = 0
    for turn in turns:
        total += 1
        if turn.sidechain is not None:
            total += _count(turn.sidechain.turns)
    return total


def to_turn(turn: TurnSegmentView, index: int) -> Turn:
    """One kept segment turn, projected onto the panel's read model at ``index``."""
    timestamp = datetime.fromisoformat(turn.timestamp) if turn.timestamp is not None else None
    if turn.kind == "tool":
        assert turn.tool is not None
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
    # Only "env"/"asst" reach here — "thinking" is filtered by `select_turns` and "tool"
    # returns above; the panel's own `TurnKind` names are unchanged either way.
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
