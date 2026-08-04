"""The panel's transcript read model, projected off the harness seam (blizzard#245).

The **only** module in ``transcripts/`` as of this change — file and record
knowledge now live behind :mod:`blizzard.runner.harness.transcript`
(``harness/internal/claude_code_normalizer.py`` and ``claude_code_transcript.py``).
:class:`ProjectedTranscriptRepository` implements
:class:`~blizzard.runner.transcripts.repository.IReadTranscriptRepository` over an
injected :class:`~blizzard.runner.harness.transcript.IHarnessTranscriptSource`, so the
``GET /api/leases/{lease_id}/transcript`` endpoint and the Angular panel that renders it
stay byte-identical with no changes of their own.

``read_turns`` makes exactly **one** ``turns_since(..., since=None)`` call —
``complete=True`` by construction (the plan's cap-reconciliation table: the
batch-budget cap never applies at ``since=None``), so this projection never loops and
peak memory stays exactly today's single tail-capped read.

Deliberately a **narrowing** projection, not a widening one — the panel's contract is
what it renders today:

* ``thinking`` turns are dropped.
* Every sidechain is dropped — both a tool turn's nested
  :attr:`~blizzard.runner.harness.transcript.NormalizedTurn.sidechain` and
  :attr:`~blizzard.runner.harness.transcript.TranscriptBatch.unlinked_sidechains`.
  Today's parser filtered every ``isSidechain`` record outright, so dropping both is
  what preserves that outcome one layer down (the phase-2 normalizer test's own
  rewrite note). Widening the panel to show either is the hub-transcript-view issue's
  job, not this one.
* :data:`MAX_TURNS` (recency, keep-newest) moves here from the old parser — a forward
  incremental read must never silently drop turns, so that cap cannot live in the
  normalizer any more.
* A tool call's structured ``input`` is re-materialized to the flattened JSON string
  the wire contract has always carried (``json.dumps``), **then** capped at
  :data:`MAX_BLOCK_CHARS` with the overflow OR'd into ``Turn.truncated`` — today the cap
  applied to the same serialized string at normalization time; under the new model
  there is no string to cap until this re-materialization step, so the cap moves with
  it. A narrow, untested-by-the-golden-fixtures accepted gap: unlike the old parser's
  blanket ``_clean`` (ANSI-strip + cap) over the serialized string, this
  re-materialization only caps — it does not re-run ANSI stripping, since a tool
  call's structured *input* practically never carries raw terminal escapes (that is a
  tool *output* phenomenon, still stripped in the normalizer, unaffected here). The
  re-materialization itself is byte-identical with the old parser's blanket
  ``json.dumps(raw_input)`` for every input shape, driven by
  :attr:`~blizzard.runner.harness.transcript.ToolCall.input_shape` rather than
  re-inspecting ``input``/``input_unparsed`` (ambiguous on its own — see that field's
  own docstring).
"""

from __future__ import annotations

import json

from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NormalizedTurn, ToolCall
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript, Turn, TurnKind

#: Keep only the most recent this-many turns (post-projection) — ported from the
#: deleted ``transcripts/parser.py``, unchanged in value.
MAX_TURNS = 1000

#: Cap a tool call's *serialized* input at this many characters — see the module
#: docstring for why this is a distinct constant from the normalizer's own
#: ``MAX_BLOCK_CHARS`` (same value, different layer, re-materialized here).
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

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return self._source.read_raw_lines(session_id, spawn_cwd=spawn_cwd)

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        return self._source.size_bytes(session_id, spawn_cwd=spawn_cwd)


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
    # `input_shape` is the explicit discriminator (`ToolInputShape`) — never guessed
    # by re-parsing `input_unparsed`, which is ambiguous whenever a bare string
    # happens to itself parse as JSON (`"123"`, `"true"`, ...). Byte-identical with
    # the old parser's blanket `json.dumps(raw_input)` for every shape but "absent",
    # which the old parser rendered as `""` rather than `json.dumps(None)`.
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


# Typecheck-time Protocol/adapter conformance sentinel (the exemplar's shape,
# `../../exemplars/python/repo_pattern.py`). Pyright rejects the return if
# `ProjectedTranscriptRepository` drifts from `IReadTranscriptRepository`.
def _conforms_read_transcript_repository(x: ProjectedTranscriptRepository) -> IReadTranscriptRepository:
    return x
