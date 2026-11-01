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
from typing import Any

from blizzard.foundation.roles import dto

#: The board's own cut: a longer input is shown to here, then an ellipsis.
_MAX_INPUT_PREVIEW_CHARS = 300
_INDENT = "  "
_NO_TIME = "—"


@dto
@dataclass(frozen=True)
class SegmentIndexLines:
    """A chunk's segment index: one line per segment, ids first so one can be handed to ``get`` or ``reship``."""

    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        segments: list[dict[str, Any]] = self.body.get("segments") or []
        if not segments:
            yield "no transcript segments"
            return
        for segment in segments:
            state = "final" if segment["final"] else "open"
            truncated = "  truncated" if segment["truncated"] else ""
            yield (
                f"{segment['segment_id']}  {segment['node_id']}  epoch {segment['epoch']}  "
                f"generation {segment['spawn_generation']}  turns {segment['turn_range_start']}-"
                f"{segment['turn_range_end']}  {state}{truncated}  {segment['byte_count']} bytes  "
                f"{segment.get('harness_id') or '-'}  {segment['received_at']}"
            )


@dto
@dataclass(frozen=True)
class SegmentContentLines:
    """One segment's turns, read top to bottom."""

    body: dict[str, Any]

    def lines(self) -> Iterator[str]:
        state = "final" if self.body["final"] else "open"
        truncated = "  truncated" if self.body["truncated"] else ""
        yield f"{self.body['segment_id']}  {state}{truncated}"
        turns: list[dict[str, Any]] = self.body.get("turns") or []
        if not turns:
            yield "no turns"
            return
        yield from _turn_lines(turns, "")


def _folded(turns: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """``turns`` with each late result and late subagent conversation moved onto the call it names. A turn whose call
    is not among ``turns`` stays where it is, so nothing read is dropped."""
    merged = [dict(turn) for turn in turns]
    anchors: dict[str, dict[str, Any]] = {}
    for turn in merged:
        tool = turn.get("tool")
        if tool and tool.get("tool_use_id") and not tool.get("output_patch"):
            anchors.setdefault(tool["tool_use_id"], turn)
    kept: list[dict[str, Any]] = []
    for turn in merged:
        tool = turn.get("tool")
        if tool and tool.get("output_patch"):
            anchor = anchors.get(tool.get("tool_use_id") or "")
            if anchor is not None:
                anchor["tool"] = {
                    **anchor["tool"],
                    "output": tool["output"],
                    "output_truncated": anchor["tool"]["output_truncated"] or tool["output_truncated"],
                }
                continue
        sidechain = turn.get("sidechain")
        if turn["kind"] == "sidechain" and sidechain and sidechain.get("parent_tool_use_id"):
            anchor = anchors.get(sidechain["parent_tool_use_id"])
            if anchor is not None and _adopts(anchor, sidechain):
                held = anchor.get("sidechain")
                anchor["sidechain"] = (
                    {**held, "turns": [*held["turns"], *sidechain["turns"]]} if held else dict(sidechain)
                )
                continue
        kept.append(turn)
    return kept


def _adopts(anchor: dict[str, Any], late: dict[str, Any]) -> bool:
    """Whether ``anchor`` can take ``late``: it holds no conversation yet, or the same agent's."""
    held = anchor.get("sidechain")
    return held is None or held.get("agent_id") == late.get("agent_id")


def _turn_lines(turns: Sequence[dict[str, Any]], indent: str) -> Iterator[str]:
    for turn in _folded(turns):
        stamp = turn.get("timestamp") or _NO_TIME
        kind = turn["kind"]
        if kind == "tool":
            yield from _tool_lines(turn, stamp, indent)
        elif kind == "thinking":
            yield f"{indent}{stamp}  thinking"
            if turn.get("thinking_redacted"):
                yield f"{indent}{_INDENT}redacted"
            else:
                yield from _block(turn["text"], indent + _INDENT)
        elif kind == "sidechain":
            sidechain = turn.get("sidechain")
            label = f" {_sidechain_label(sidechain)}" if sidechain else ""
            yield f"{indent}{stamp}  sidechain{label}"
            if sidechain:
                yield from _turn_lines(sidechain["turns"], indent + _INDENT)
        else:
            yield f"{indent}{stamp}  {'env' if kind == 'env' else 'assistant'}"
            yield from _block(turn["text"], indent + _INDENT)
        if turn.get("truncated"):
            yield f"{indent}{_INDENT}⋯ truncated"


def _tool_lines(turn: dict[str, Any], stamp: str, indent: str) -> Iterator[str]:
    tool = turn["tool"]
    head = f"{indent}{stamp}  tool {tool['name']}"
    preview = _input_preview(tool)
    yield f"{head}  {preview}" if preview else head
    output = tool.get("output")
    if output is None:
        yield f"{indent}{_INDENT}running…"
    else:
        yield from _block(output, indent + _INDENT)
    if tool.get("output_truncated"):
        yield f"{indent}{_INDENT}⋯ output truncated"
    sidechain = turn.get("sidechain")
    if sidechain:
        yield f"{indent}{_INDENT}sidechain {_sidechain_label(sidechain)}"
        yield from _turn_lines(sidechain["turns"], indent + _INDENT * 2)


def _block(text: str, indent: str) -> Iterator[str]:
    for line in text.splitlines() or [""]:
        yield f"{indent}{line}" if line else ""


def _input_preview(tool: dict[str, Any]) -> str:
    raw = json.dumps(tool["input"]) if tool.get("input_shape") == "object" else tool.get("input_unparsed") or ""
    return raw if len(raw) <= _MAX_INPUT_PREVIEW_CHARS else f"{raw[:_MAX_INPUT_PREVIEW_CHARS]}…"


def _sidechain_label(sidechain: dict[str, Any]) -> str:
    who = sidechain.get("agent_type") or sidechain.get("agent_id") or "subagent"
    return f"{who} · unlinked" if sidechain.get("link") == "unlinked" else who
