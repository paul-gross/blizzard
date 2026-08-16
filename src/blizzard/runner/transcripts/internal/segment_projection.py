"""Maps a hub segment's wire turns onto the runner's transcript read model (blizzard#249).

One-to-one, not a narrowing: blizzard#248 D1/D2 widened :class:`Turn` to the segment wire's
own shape, so thinking turns, tool calls and nested sidechains all survive the trip. Total
rather than raising — a body validating against :class:`TurnSegmentView` is proven
well-typed, never internally consistent, so a bad field degrades past this 200-always seam."""

from __future__ import annotations

from datetime import datetime

from blizzard.runner.transcripts.repository import Sidechain, ToolCall, Turn
from blizzard.wire.transcript_segment import SidechainSegmentView, ToolCallSegmentView, TurnSegmentView


def _parse_timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _tool(tool: ToolCallSegmentView) -> ToolCall:
    return ToolCall(
        name=tool.name,
        input=dict(tool.input),
        input_unparsed=tool.input_unparsed,
        input_shape=tool.input_shape,
        tool_use_id=tool.tool_use_id,
        output=tool.output,
        output_truncated=tool.output_truncated,
        output_patch=tool.output_patch,
    )


def _sidechain(sidechain: SidechainSegmentView) -> Sidechain:
    return Sidechain(
        agent_id=sidechain.agent_id,
        agent_type=sidechain.agent_type,
        link=sidechain.link,
        turns=[to_turn(turn, i) for i, turn in enumerate(sidechain.turns)],
        parent_tool_use_id=sidechain.parent_tool_use_id,
    )


def to_turn(turn: TurnSegmentView, index: int) -> Turn:
    """One segment turn as a domain :class:`Turn`, renumbered to ``index`` — the read's own
    window numbering, per :class:`TurnSegmentView`'s own contract. A tool call's own
    block-level loss folds into :attr:`Turn.truncated`, the way the local path folds it, so
    the same turn carries the same truncation note whichever home served it."""
    tool = turn.tool
    return Turn(
        index=index,
        kind=turn.kind,
        timestamp=_parse_timestamp(turn.timestamp),
        text=turn.text,
        tool=_tool(tool) if tool is not None else None,
        thinking_redacted=turn.thinking_redacted,
        sidechain=_sidechain(turn.sidechain) if turn.sidechain is not None else None,
        truncated=turn.truncated or (tool is not None and (tool.input_truncated or tool.output_truncated)),
    )
