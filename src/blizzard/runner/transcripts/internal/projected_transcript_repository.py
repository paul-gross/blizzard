"""The panel's transcript read model, projected off the harness seam (blizzard#245).

The **only adapter** in ``transcripts/`` — file and record knowledge lives behind
:mod:`blizzard.runner.harness.transcript` (``harness/internal/claude_code_normalizer.py``
and ``claude_code_transcript.py``); :mod:`~blizzard.runner.transcripts.repository` and
:mod:`~blizzard.runner.transcripts.service` still own the domain types and the
controller-facing read model (see :mod:`blizzard.runner.transcripts`'s own docstring).
:class:`ProjectedTranscriptRepository` implements
:class:`~blizzard.runner.transcripts.repository.IReadTranscriptRepository` over an
injected :class:`~blizzard.runner.harness.transcript.IHarnessTranscriptSource`, so the
``GET /api/leases/{lease_id}/transcript`` endpoint and the Angular panel that renders it
stay byte-identical with no changes of their own (**except** that a tool call's
re-materialized ``input`` skips the ANSI-stripping pass the rest of this projection
applies — a documented, practically-inert gap; see below).

``read_turns`` makes exactly **one** ``turns_since(..., since=None)`` call — a cold
read, which the source always reports ``complete=True`` for
(:mod:`~blizzard.runner.harness.internal.claude_code_transcript`'s own docstring), so
this projection never loops. That
call does mint a ``next_position`` like any other, but this projection has nothing to
loop on it for and discards it. Peak memory is **not** today's single tail-capped read,
though: a cold call also fans out to every sidecar the main file's tool results name,
spending its own shared budget doing so (``claude_code_transcript.MAX_BATCH_BYTES``) —
work this projection then discards in full, since it never reads a turn's
``.sidechain`` or a batch's ``unlinked_sidechains``. That fan-out's own budget
exhaustion sets ``TranscriptBatch.sidechain_truncated``, a field kept separate from
``TranscriptBatch.truncated`` for exactly this reason — this projection reads only the
latter into ``Transcript.truncated`` (below), so a sidechain budget it never renders
running out never surfaces as a false truncation banner on content that was not, in
fact, cut. A narrowing consumer paying to discover conversations it always discards is a
known, accepted cost of one seam serving both a narrowing and (eventually) a widening
reader (``blizzard-context:/verification/blizzard.md`` §Test tiers), not a defect in
either.

Deliberately a **narrowing** projection, not a widening one — the panel's contract is
what it renders today:

* ``thinking`` turns and every sidechain are dropped — both a tool turn's nested
  :attr:`~blizzard.runner.harness.transcript.NormalizedTurn.sidechain` and
  :attr:`~blizzard.runner.harness.transcript.TranscriptBatch.unlinked_sidechains`.
  Widening the panel to show either is `epic:transcripts`'s
  (``blizzard-product:/plans/transcripts.md``) job, not this one. Pinned by
  tests/test_runner_transcripts.py::test_a_thinking_turn_produces_zero_panel_turns_not_an_empty_asst_turn
  and ::test_a_sidecar_backed_sidechain_produces_zero_extra_panel_turns.
* :data:`MAX_TURNS` (recency, keep-newest) lives here, not in the normalizer — a
  forward incremental read must never silently drop turns, so the recency cap belongs
  at the point where a read's turns are already fully accumulated.
* A tool call's structured ``input`` is re-materialized to the flattened JSON string
  the wire contract carries (``json.dumps``), **then** capped at
  :data:`MAX_BLOCK_CHARS` with the overflow OR'd into ``Turn.truncated`` — a mapping
  has no string to cap until this re-materialization step, so the cap lives here
  rather than in the normalizer. A narrow, untested-by-the-golden-fixtures accepted
  gap: this re-materialization only caps — it does not run ANSI stripping, since a
  tool call's structured *input* practically never carries raw terminal escapes (that
  is a tool *output* phenomenon, still stripped in the normalizer, unaffected here).
  The re-materialization is driven by
  :attr:`~blizzard.runner.harness.transcript.ToolCall.input_shape` rather than
  re-inspecting ``input``/``input_unparsed`` (ambiguous on its own — see that field's
  own docstring), and is byte-identical with the wire contract's ``json.dumps`` shape
  for every input shape.
"""

from __future__ import annotations

import json

from blizzard.runner.harness.transcript import IHarnessTranscriptSource, NormalizedTurn, ToolCall
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript, Turn, TurnKind

#: Keep only the most recent this-many turns (post-projection) — bounds the panel
#: payload to the newest, most relevant conversation on a long-running session.
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
    # the wire contract's blanket `json.dumps(raw_input)` for every shape but
    # "absent", which the wire contract renders as `""` rather than `json.dumps(None)`.
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
# `blizzard-context:/exemplars/python/repo_pattern.py`). Pyright rejects the return if
# `ProjectedTranscriptRepository` drifts from `IReadTranscriptRepository`.
def _conforms_read_transcript_repository(x: ProjectedTranscriptRepository) -> IReadTranscriptRepository:
    return x
