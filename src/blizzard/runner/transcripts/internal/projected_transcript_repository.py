"""The panel's transcript read model, projected off the harness seam (blizzard#245).

Deliberately a **narrowing** projection: ``thinking`` turns and every sidechain are
dropped, the recency cap (:data:`MAX_TURNS`) applies once turns are fully accumulated,
and a tool call's structured ``input`` is re-materialized to the wire contract's JSON
string, then capped — a mapping has no string to cap before that step."""

from __future__ import annotations

import json
from dataclasses import dataclass

from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NormalizedTurn, ToolCall
from blizzard.runner.transcripts.repository import (
    MAX_BLOCK_CHARS,
    MAX_TURNS,
    IReadTranscriptRepository,
    Transcript,
    Turn,
    TurnKind,
)


@dataclass(frozen=True)
class SerializedInput:
    """A tool call's ``input`` re-materialized to the wire contract's JSON string, and capped."""

    text: str
    truncated: bool

    @classmethod
    def of(cls, tool: ToolCall) -> SerializedInput:
        # `input_shape` is the explicit discriminator — never guessed by re-parsing
        # `input_unparsed`, ambiguous whenever a bare string itself parses as JSON.
        if tool.input_shape == "absent":
            serialized = ""
        elif tool.input_shape == "string":
            assert tool.input_unparsed is not None
            serialized = json.dumps(tool.input_unparsed)
        elif tool.input_shape == "other":
            assert tool.input_unparsed is not None
            serialized = tool.input_unparsed
        else:
            serialized = json.dumps(tool.input)
        if len(serialized) > MAX_BLOCK_CHARS:
            return cls(serialized[:MAX_BLOCK_CHARS], True)
        return cls(serialized, False)


@dataclass(frozen=True)
class ProjectedTurn:
    """One normalized turn narrowed to the panel's read model."""

    source: NormalizedTurn

    def at(self, index: int) -> Turn:
        """The read-model turn at ``index`` — assigned only once the recency cap has
        decided which turns survive."""
        turn = self.source
        if turn.kind == "tool":
            assert turn.tool is not None
            serialized = SerializedInput.of(turn.tool)
            return Turn(
                index=index,
                kind="tool",
                timestamp=turn.timestamp,
                text="",
                tool_name=turn.tool.name,
                tool_input=serialized.text,
                tool_output=turn.tool.output,
                truncated=turn.truncated or turn.tool.output_truncated or serialized.truncated,
            )
        # Only "env"/"asst" reach here — "thinking" is filtered before this is called and
        # "tool" returns above; the panel's own `TurnKind` names are unchanged either way.
        kind: TurnKind = "env" if turn.kind == "env" else "asst"
        return Turn(
            index=index,
            kind=kind,
            timestamp=turn.timestamp,
            text=turn.text,
            tool_name=None,
            tool_input=None,
            tool_output=None,
            truncated=turn.truncated,
        )


class ProjectedTranscriptRepository:
    """Implements :class:`IReadTranscriptRepository` over an injected
    :class:`IHarnessTranscriptSource` (``bzh:dependency-inversion``)."""

    def __init__(self, source: IHarnessTranscriptSource) -> None:
        self._source = source

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        batch = self._source.turns_since(session_id, spawn_cwd=spawn_cwd, since=None)
        if not batch.available:
            return Transcript(session_id=session_id, available=False, reason=batch.reason, turns=[], truncated=False)

        projected = [ProjectedTurn(t) for t in batch.turns if t.kind != "thinking"]
        turns_truncated = len(projected) > MAX_TURNS
        kept = projected[-MAX_TURNS:] if turns_truncated else projected
        return Transcript(
            session_id=session_id,
            available=True,
            reason=None,
            turns=[p.at(i) for i, p in enumerate(kept)],
            truncated=turns_truncated or batch.truncated,
        )


# Typecheck-time Protocol/adapter conformance sentinel
# (`blizzard-context:/exemplars/python/repo_pattern.py`).
def _conforms_read_transcript_repository(x: ProjectedTranscriptRepository) -> IReadTranscriptRepository:
    return x
