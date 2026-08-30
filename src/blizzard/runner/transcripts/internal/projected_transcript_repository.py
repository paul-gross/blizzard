"""The panel's transcript read model, projected off the harness seam (blizzard#245, widened
blizzard#248 D1/D2) — no longer narrowing: thinking turns and sidechains carry through.

:data:`MAX_TURNS` bounds only the top-level list, never a sidechain's own turns.
:data:`MAX_BLOCK_CHARS` degrades only an oversized tool input to a capped raw string."""

from __future__ import annotations

import json
from dataclasses import dataclass

from blizzard.runner.harness.transcript import (
    IHarnessTranscriptSource,
    NormalizedTurn,
    SidechainConversation,
    TranscriptPosition,
)
from blizzard.runner.harness.transcript import ToolCall as HarnessToolCall
from blizzard.runner.transcripts.repository import (
    IReadTranscriptRepository,
    Sidechain,
    ToolCall,
    Transcript,
    Turn,
)

#: Keep only the most recent this-many top-level turns — an unlinked sidechain's synthetic
#: top-level turn (:func:`_unlinked_turn`) is appended after this cap, uncounted.
MAX_TURNS = 1000

#: Cap a tool call's serialized input before it degrades to a raw string (:class:`CappedToolCall`).
MAX_BLOCK_CHARS = 1024 * 1024


@dataclass(frozen=True)
class CappedToolCall:
    """A tool call's ``input``, degraded to a capped raw string only once its serialized form
    would exceed :data:`MAX_BLOCK_CHARS` — the structured mapping (or ``input_unparsed``)
    passes through untouched below that bound."""

    call: ToolCall
    truncated: bool

    @classmethod
    def of(cls, tool: HarnessToolCall) -> CappedToolCall:
        serialized = json.dumps(tool.input) if tool.input_shape == "object" else (tool.input_unparsed or "")
        if len(serialized) <= MAX_BLOCK_CHARS:
            return cls(
                ToolCall(
                    name=tool.name,
                    input=tool.input,
                    input_unparsed=tool.input_unparsed,
                    input_shape=tool.input_shape,
                    tool_use_id=tool.tool_use_id,
                    output=tool.output,
                    output_truncated=tool.output_truncated,
                ),
                False,
            )
        return cls(
            ToolCall(
                name=tool.name,
                input={},
                input_unparsed=serialized[:MAX_BLOCK_CHARS],
                input_shape="other",
                tool_use_id=tool.tool_use_id,
                output=tool.output,
                output_truncated=tool.output_truncated,
            ),
            True,
        )


def _turn(source: NormalizedTurn, index: int) -> Turn:
    """One normalized turn to the read model, at a caller-assigned ``index`` — recursing into
    a tool turn's sidechain, whose own turns index independently from 0 and are never trimmed
    by :data:`MAX_TURNS`."""
    tool: ToolCall | None = None
    sidechain: Sidechain | None = None
    block_truncated = False
    if source.kind == "tool":
        assert source.tool is not None
        capped = CappedToolCall.of(source.tool)
        tool = capped.call
        block_truncated = capped.truncated or source.tool.output_truncated
        if source.sidechain is not None:
            sidechain = _sidechain(source.sidechain)
    return Turn(
        index=index,
        kind=source.kind,
        timestamp=source.timestamp,
        text=source.text,
        tool=tool,
        thinking_redacted=source.thinking_redacted,
        sidechain=sidechain,
        truncated=source.truncated or block_truncated,
    )


def _sidechain(source: SidechainConversation) -> Sidechain:
    return Sidechain(
        agent_id=source.agent_id,
        agent_type=source.agent_type,
        link=source.link,
        turns=[_turn(t, i) for i, t in enumerate(source.turns)],
    )


def _unlinked_turn(source: SidechainConversation, index: int) -> Turn:
    """An unlinked sidechain (no spawning tool call resolved) as its own top-level
    ``"sidechain"`` turn — the one turn kind with no harness-side counterpart, minted here
    because :class:`~blizzard.runner.harness.transcript.NormalizedTurn` has no slot for a
    sidechain with nothing to nest under."""
    return Turn(
        index=index,
        kind="sidechain",
        timestamp=None,
        text="",
        tool=None,
        thinking_redacted=False,
        sidechain=_sidechain(source),
        truncated=False,
    )


class ProjectedTranscriptRepository:
    """Implements :class:`IReadTranscriptRepository` over an injected
    :class:`IHarnessTranscriptSource` (``bzh:dependency-inversion``)."""

    def __init__(self, source: IHarnessTranscriptSource) -> None:
        self._source = source

    def read_turns(self, session_id: str, *, spawn_cwd: str | None, since: str | None = None) -> Transcript:
        position = TranscriptPosition(since) if since is not None else None
        batch = self._source.turns_since(session_id, spawn_cwd=spawn_cwd, since=position)
        if not batch.available:
            return Transcript(session_id=session_id, available=False, reason=batch.reason, turns=[], truncated=False)

        turns_truncated = len(batch.turns) > MAX_TURNS
        kept = batch.turns[-MAX_TURNS:] if turns_truncated else batch.turns
        projected = [_turn(t, i) for i, t in enumerate(kept)]
        start = len(projected)
        projected.extend(_unlinked_turn(sc, start + i) for i, sc in enumerate(batch.unlinked_sidechains))
        return Transcript(
            session_id=session_id,
            available=True,
            reason=None,
            turns=projected,
            truncated=turns_truncated or batch.truncated or batch.sidechain_truncated,
        )


# Typecheck-time Protocol/adapter conformance sentinel
# (`blizzard-context:/exemplars/python/repo_pattern.py`).
def _conforms_read_transcript_repository(x: ProjectedTranscriptRepository) -> IReadTranscriptRepository:
    return x
