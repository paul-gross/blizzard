"""``Turn`` (the local transcript read model) -> ``TurnSegmentView`` (the wire projection) —
shared by every runner-plane route rendering a local transcript onto the wire, lease-keyed
(``src/blizzard/runner/api/transcripts.py``) or segment-keyed (``src/blizzard/runner/api/transcript_segments.py``)."""

from __future__ import annotations

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.transcripts.repository import Sidechain, ToolCall, Turn
from blizzard.wire.transcript_segment import SidechainSegmentView, ToolCallSegmentView, TurnSegmentView


def tool_view(tool: ToolCall) -> ToolCallSegmentView:
    return ToolCallSegmentView(
        name=tool.name,
        input=dict(tool.input),
        input_unparsed=tool.input_unparsed,
        input_shape=tool.input_shape,
        tool_use_id=tool.tool_use_id,
        output=tool.output,
        output_truncated=tool.output_truncated,
        output_patch=tool.output_patch,
    )


def sidechain_view(sidechain: Sidechain) -> SidechainSegmentView:
    return SidechainSegmentView(
        agent_id=sidechain.agent_id,
        agent_type=sidechain.agent_type,
        link=sidechain.link,
        turns=[turn_view(turn) for turn in sidechain.turns],
        parent_tool_use_id=sidechain.parent_tool_use_id,
    )


def turn_view(turn: Turn) -> TurnSegmentView:
    return TurnSegmentView(
        index=turn.index,
        kind=turn.kind,
        timestamp=iso_utc(turn.timestamp) if turn.timestamp is not None else None,
        text=turn.text,
        tool=tool_view(turn.tool) if turn.tool is not None else None,
        thinking_redacted=turn.thinking_redacted,
        sidechain=sidechain_view(turn.sidechain) if turn.sidechain is not None else None,
        truncated=turn.truncated,
    )
