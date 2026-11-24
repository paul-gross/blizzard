"""The terminal rendering of a transcript read — shared by the hub and runner CLIs, which both print the
``TranscriptSegmentIndexView`` and ``TranscriptSegmentContentView`` bodies their routes answer with.

It shows what the board's transcript viewer shows: one block per turn with its timestamp and kind; a tool turn's name,
input preview and output; a redacted thinking turn marked so; a sidechain indented under its parent; a truncated turn
noted. Like the board, it folds a late-shipped result (``output_patch``) and a late-shipped subagent conversation
(``parent_tool_use_id``) back onto the call they belong to."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import dto
from blizzard.wire.transcript_segment import (
    SidechainSegmentView,
    ToolCallSegmentView,
    TranscriptSegmentContentView,
    TranscriptSegmentIndexView,
    TurnSegmentView,
)

#: The board's own cut: a longer input is shown to here, then an ellipsis.
_MAX_INPUT_PREVIEW_CHARS = 300
_INDENT = "  "
_NO_TIME = "—"


@dto
@dataclass(frozen=True)
class SegmentIndexLines:
    """A chunk's segment index: one line per segment, ids first so one can be handed to ``get`` or ``reship``."""

    body: TranscriptSegmentIndexView

    def lines(self) -> Iterator[str]:
        if not self.body.segments:
            yield "no transcript segments"
            return
        for segment in self.body.segments:
            state = "final" if segment.final else "open"
            truncated = "  truncated" if segment.truncated else ""
            yield (
                f"{segment.segment_id}  {segment.node_id}  epoch {segment.epoch}  "
                f"generation {segment.spawn_generation}  turns {segment.turn_range_start}-"
                f"{segment.turn_range_end}  {state}{truncated}  {segment.byte_count} bytes  "
                f"{segment.harness_id or '-'}  {segment.received_at}"
            )


@dto
@dataclass(frozen=True)
class SegmentContentLines:
    """One segment's turns, read top to bottom."""

    body: TranscriptSegmentContentView

    def lines(self) -> Iterator[str]:
        state = "final" if self.body.final else "open"
        truncated = "  truncated" if self.body.truncated else ""
        yield f"{self.body.segment_id}  {state}{truncated}"
        if not self.body.turns:
            yield "no turns"
            return
        yield from _turn_lines(self.body.turns, "")


def _folded(turns: Sequence[TurnSegmentView]) -> list[TurnSegmentView]:
    """``turns`` with each late result and late subagent conversation moved onto the call it names. A turn whose call
    is not among ``turns`` stays where it is, so nothing read is dropped. The decoded turns are never mutated: an
    anchor that takes a late piece is replaced by a copy."""
    merged = list(turns)
    anchors: dict[str, int] = {}
    for position, turn in enumerate(merged):
        tool = turn.tool
        if tool and tool.tool_use_id and not tool.output_patch:
            anchors.setdefault(tool.tool_use_id, position)
    kept: list[int] = []
    for position, turn in enumerate(turns):
        tool = turn.tool
        if tool and tool.output_patch:
            at = anchors.get(tool.tool_use_id or "")
            anchor = merged[at] if at is not None else None
            if at is not None and anchor is not None and anchor.tool is not None:
                merged[at] = anchor.model_copy(
                    update={
                        "tool": anchor.tool.model_copy(
                            update={
                                "output": tool.output,
                                "output_truncated": anchor.tool.output_truncated or tool.output_truncated,
                            }
                        )
                    }
                )
                continue
        sidechain = turn.sidechain
        if turn.kind == "sidechain" and sidechain and sidechain.parent_tool_use_id:
            at = anchors.get(sidechain.parent_tool_use_id)
            if at is not None and _adopts(merged[at], sidechain):
                held = merged[at].sidechain
                adopted = held.model_copy(update={"turns": [*held.turns, *sidechain.turns]}) if held else sidechain
                merged[at] = merged[at].model_copy(update={"sidechain": adopted})
                continue
        kept.append(position)
    return [merged[position] for position in kept]


def _adopts(anchor: TurnSegmentView, late: SidechainSegmentView) -> bool:
    """Whether ``anchor`` can take ``late``: it holds no conversation yet, or the same agent's."""
    held = anchor.sidechain
    return held is None or held.agent_id == late.agent_id


def _turn_lines(turns: Sequence[TurnSegmentView], indent: str) -> Iterator[str]:
    for turn in _folded(turns):
        stamp = turn.timestamp or _NO_TIME
        kind = turn.kind
        if turn.tool is not None and kind == "tool":
            yield from _tool_lines(turn, turn.tool, stamp, indent)
        elif kind == "thinking":
            yield f"{indent}{stamp}  thinking"
            if turn.thinking_redacted:
                yield f"{indent}{_INDENT}redacted"
            else:
                yield from _block(turn.text, indent + _INDENT)
        elif kind == "sidechain":
            sidechain = turn.sidechain
            label = f" {_sidechain_label(sidechain)}" if sidechain else ""
            yield f"{indent}{stamp}  sidechain{label}"
            if sidechain:
                yield from _turn_lines(sidechain.turns, indent + _INDENT)
        else:
            yield f"{indent}{stamp}  {'env' if kind == 'env' else 'assistant'}"
            yield from _block(turn.text, indent + _INDENT)
        if turn.truncated:
            yield f"{indent}{_INDENT}⋯ truncated"


def _tool_lines(turn: TurnSegmentView, tool: ToolCallSegmentView, stamp: str, indent: str) -> Iterator[str]:
    head = f"{indent}{stamp}  tool {tool.name}"
    preview = _input_preview(tool)
    yield f"{head}  {preview}" if preview else head
    if tool.output is None:
        yield f"{indent}{_INDENT}running…"
    else:
        yield from _block(tool.output, indent + _INDENT)
    if tool.output_truncated:
        yield f"{indent}{_INDENT}⋯ output truncated"
    sidechain = turn.sidechain
    if sidechain:
        yield f"{indent}{_INDENT}sidechain {_sidechain_label(sidechain)}"
        yield from _turn_lines(sidechain.turns, indent + _INDENT * 2)


def _block(text: str, indent: str) -> Iterator[str]:
    for line in text.splitlines() or [""]:
        yield f"{indent}{line}" if line else ""


def _input_preview(tool: ToolCallSegmentView) -> str:
    raw = json.dumps(tool.input) if tool.input_shape == "object" else tool.input_unparsed or ""
    return raw if len(raw) <= _MAX_INPUT_PREVIEW_CHARS else f"{raw[:_MAX_INPUT_PREVIEW_CHARS]}…"


def _sidechain_label(sidechain: SidechainSegmentView) -> str:
    who = sidechain.agent_type or sidechain.agent_id or "subagent"
    return f"{who} · unlinked" if sidechain.link == "unlinked" else who
