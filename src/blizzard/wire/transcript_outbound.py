"""Runner→hub transcript delta intake (issue #246) — the dedicated outbound lane's own
wire, alongside ``wire/facts.py``'s fact-lane pair. Structurally independent of it (D3): a
batched push whose per-runner seq applies idempotently against the transcript lane's own
high-water mark. A segment's final marker rides the same batch as a distinctly-kinded
fact, following ``RunnerFact``'s own ``kind``/``payload`` split rather than a second
route."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

#: Delta kinds the batched /transcripts push accepts (``noun.verb`` names, matching
#: ``wire/facts.py``'s vocabulary).
TRANSCRIPT_DELTA = "transcript.delta"
TRANSCRIPT_FINAL = "transcript.final"

#: The per-record cap (plan D4) — truncated in place runner-side, rejected-but-acked
#: hub-side if one ever ships over-cap anyway.
TRANSCRIPT_RECORD_MAX_BYTES = 1024 * 1024


class TranscriptFact(BaseModel):
    """One buffered transcript fact: its per-runner seq, its kind, and its payload.

    ``payload`` is kind-specific and kept open, matching ``RunnerFact`` — a delta's shape
    is a later phase's concern; this wire model does not change when it lands."""

    seq: int
    kind: str
    payload: dict[str, Any] = {}


class TranscriptFactBatch(BaseModel):
    """A runner's push of one-or-more buffered transcript facts, ordered by seq — the
    transcript lane's own batch, never merged onto ``RunnerFactBatch`` (D3)."""

    runner_id: str
    facts: list[TranscriptFact]


class TranscriptFactAck(BaseModel):
    """The hub's per-batch acknowledgement against its own transcript high-water mark —
    the transcript-lane counterpart to ``RunnerFactAck``, over a mark ``wire/facts.py``'s
    ack never touches."""

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    rejected: list[int] = []
