"""Projects a hub segment's turns onto the panel's read model (blizzard#249, D5).

Narrows like the local path — drops ``thinking`` turns and sidechains wholesale, but
counts what it drops. Total rather than raising: a malformed turn (``kind="tool"`` with
no payload, an ``input_shape``/``input_unparsed`` pairing only the local harness
normalizer's own contract guarantees, an unparseable ``timestamp``) degrades in place
instead of reaching this 200-always route as an exception — the wire body validating
against :class:`TurnSegmentView` proves it is well-typed, never that it is internally
consistent."""

from __future__ import annotations

import json
from datetime import datetime

from blizzard.runner.transcripts.repository import MAX_BLOCK_CHARS, Turn, TurnKind
from blizzard.wire.transcript_segment import ToolCallSegmentView, TurnSegmentView


def select_turns(turns: list[TurnSegmentView]) -> tuple[list[tuple[TurnSegmentView, int]], int]:
    """The segment's turns narrowed to the panel's kept set, each survivor paired with how
    many turns its own drop absorbed — thinking/malformed turns dropped just before it,
    plus its own sidechain's nested count — so a caller applying the recency cap after
    this call totals only the drops the surviving turns it kept actually carry. The second
    return value is every drop *after* the last surviving turn (or every drop, when none
    survive): always part of the rendered window regardless of the cap, since nothing
    comes after it for the cap to discard."""
    kept: list[tuple[TurnSegmentView, int]] = []
    pending = 0
    for turn in turns:
        if turn.kind == "thinking" or (turn.kind == "tool" and turn.tool is None):
            pending += 1
            continue
        own = _count(turn.sidechain.turns) if turn.sidechain is not None else 0
        kept.append((turn, pending + own))
        pending = 0
    return kept, pending


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


def _serialize_tool_input(tool: ToolCallSegmentView) -> tuple[str, bool]:
    """The wire tool call's ``input``, serialized to the same JSON-string contract the
    local path's ``SerializedInput`` produces for a locally-normalized turn — but total.
    ``SerializedInput`` asserts ``input_unparsed is not None`` for ``"string"``/``"other"``
    shapes, a pairing only the local harness normalizer's own contract establishes; a
    wire-sourced turn carries no such guarantee, so an unpairable combination degrades to
    the structured ``input`` instead of asserting."""
    if tool.input_shape == "absent":
        serialized = ""
    elif tool.input_shape == "string" and tool.input_unparsed is not None:
        serialized = json.dumps(tool.input_unparsed)
    elif tool.input_shape == "other" and tool.input_unparsed is not None:
        serialized = tool.input_unparsed
    else:
        serialized = json.dumps(tool.input)
    if len(serialized) > MAX_BLOCK_CHARS:
        return serialized[:MAX_BLOCK_CHARS], True
    return serialized, False


def to_turn(turn: TurnSegmentView, index: int) -> Turn:
    """One kept segment turn, projected onto the panel's read model at ``index``. Total:
    a ``kind="tool"`` turn with no payload degrades to plain text rather than the old bare
    ``assert``, an unparseable ``timestamp`` degrades to ``None``, and an unpairable tool
    input degrades via :func:`_serialize_tool_input` instead of raising."""
    timestamp = _parse_timestamp(turn.timestamp)
    if turn.kind == "tool" and turn.tool is not None:
        text, input_truncated = _serialize_tool_input(turn.tool)
        return Turn(
            index=index,
            kind="tool",
            timestamp=timestamp,
            text="",
            tool_name=turn.tool.name,
            tool_input=text,
            tool_output=turn.tool.output,
            truncated=turn.truncated or turn.tool.output_truncated or input_truncated,
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
