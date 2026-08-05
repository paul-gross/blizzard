"""The panel's transcript read model, projected off the harness seam (blizzard#245).

Deliberately a **narrowing** projection: ``thinking`` turns and every sidechain are
dropped, the recency cap (:data:`MAX_TURNS`) applies once turns are fully accumulated,
and a tool call's structured ``input`` is re-materialized to the wire contract's JSON
string, then capped — a mapping has no string to cap before that step."""

from __future__ import annotations

import json

from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NormalizedTurn, ToolCall
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript, Turn, TurnKind

#: Keep only the most recent this-many turns (post-projection) — bounds the panel
#: payload to the newest, most relevant conversation on a long-running session.
MAX_TURNS = 1000

#: Cap a tool call's *serialized* input at this many characters — the normalizer's own
#: cap is a different layer, before re-materialization.
MAX_BLOCK_CHARS = 1024 * 1024


class ProjectedTranscriptRepository:
    """Implements :class:`IReadTranscriptRepository` over an injected
    :class:`IHarnessTranscriptSource` (``bzh:dependency-inversion``)."""

    def __init__(self, source: IHarnessTranscriptSource) -> None:
        self._source = source

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        batch = self._source.turns_since(session_id, spawn_cwd=spawn_cwd, since=None)
        if not batch.available:
            return Transcript(session_id=session_id, available=False, reason=batch.reason, turns=[], truncated=False)

        projected = [_project_turn(t) for t in batch.turns if t.kind != "thinking"]

        turns_truncated = len(projected) > MAX_TURNS
        kept = projected[-MAX_TURNS:] if turns_truncated else projected
        reindexed = [
            Turn(
                index=i,
                kind=t.kind,
                timestamp=t.timestamp,
                text=t.text,
                tool_name=t.tool_name,
                tool_input=t.tool_input,
                tool_output=t.tool_output,
                truncated=t.truncated,
            )
            for i, t in enumerate(kept)
        ]
        return Transcript(
            session_id=session_id,
            available=True,
            reason=None,
            turns=reindexed,
            truncated=turns_truncated or batch.truncated,
        )


def _project_turn(turn: NormalizedTurn) -> Turn:
    if turn.kind == "tool":
        assert turn.tool is not None
        tool_input, input_truncated = _serialize_tool_input(turn.tool)
        return Turn(
            index=0,  # reassigned by the caller after `MAX_TURNS` and re-indexing
            kind="tool",
            timestamp=turn.timestamp,
            text="",
            tool_name=turn.tool.name,
            tool_input=tool_input,
            tool_output=turn.tool.output,
            truncated=turn.truncated or turn.tool.output_truncated or input_truncated,
        )
    # Only "env"/"asst" reach here — "thinking" is filtered before this is called and
    # "tool" returns above; the panel's own `TurnKind` names are unchanged either way.
    kind: TurnKind = "env" if turn.kind == "env" else "asst"
    return Turn(
        index=0,
        kind=kind,
        timestamp=turn.timestamp,
        text=turn.text,
        tool_name=None,
        tool_input=None,
        tool_output=None,
        truncated=turn.truncated,
    )


def _serialize_tool_input(tool: ToolCall) -> tuple[str, bool]:
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
        return serialized[:MAX_BLOCK_CHARS], True
    return serialized, False


# Typecheck-time Protocol/adapter conformance sentinel
# (`blizzard-context:/exemplars/python/repo_pattern.py`).
def _conforms_read_transcript_repository(x: ProjectedTranscriptRepository) -> IReadTranscriptRepository:
    return x
