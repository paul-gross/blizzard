"""Transcript segment wire bodies (blizzard#247, ``epic:transcripts``) — the first wire
projection of #245's normalized turn model onto shipped, hub-stored content.

A record is one shipped **turn-range slice** of a segment (D1). ``seq`` is the lane's
high-water sequence (D7); ``(segment_id, turn_range_start)`` is the natural key a
re-offer under a fresh seq dedupes against (D8). ``wire/transcript.py`` is untouched."""

from __future__ import annotations

from pydantic import BaseModel


class ToolCallSegmentView(BaseModel):
    """A tool invocation, structured: what was called, with what input, and what came back."""

    name: str
    input: dict[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool


class SidechainSegmentView(BaseModel):
    """A subagent's private conversation, nested under the tool call that spawned it.
    Recursive: a sidechain turn may itself carry a tool call whose own sidechain nests
    further."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[TurnSegmentView]


class TurnSegmentView(BaseModel):
    """One normalized turn, carried in full. ``index`` is **segment-relative** and minted
    by the producer (D9), so it is stable across the batches a segment arrives in."""

    index: int
    kind: str  # env | asst | tool | thinking
    timestamp: str | None
    text: str
    tool: ToolCallSegmentView | None
    thinking_redacted: bool
    sidechain: SidechainSegmentView | None
    truncated: bool


SidechainSegmentView.model_rebuild()


class TranscriptSegmentRecord(BaseModel):
    """One shipped turn-range slice of a segment (D1). ``final=True`` marks the one
    record that closes the segment out. ``record_truncated`` is the runner's own
    declaration of an accepted, hub-cap-conforming record shipped with ``turns``
    emptied — distinct from the hub's own ``rejected`` (D5/D6)."""

    seq: int
    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    normalizer_version: str
    harness_version: str | None
    record_truncated: bool = False
    turns: list[TurnSegmentView]


class TranscriptSegmentBatch(BaseModel):
    """A runner's push of one-or-more buffered transcript records, ordered by ``seq`` —
    the transcript lane's own store-and-forward batch, distinct from the fact lane's
    ``RunnerFactBatch`` (D7)."""

    runner_id: str
    records: list[TranscriptSegmentRecord]


class TranscriptSegmentAck(BaseModel):
    """The hub's per-batch acknowledgement against the transcript lane's high-water mark.

    ``capped`` is D6's cap-rejection class — acknowledged, content-dropped, and the
    high-water advances past it, a durable decision that must not re-adjudicate on replay."""

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    capped: list[int] = []


class TranscriptSegmentIndexEntry(BaseModel):
    """One segment's metadata row (D12) — byte counts and completion state, never turn
    content. ``truncated`` marks a segment the per-segment cap rejected part of (D5), so a
    consumer can tell an incomplete segment from a short one without fetching it."""

    segment_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    truncated: bool
    byte_count: int
    normalizer_version: str
    harness_version: str | None
    received_at: str


class TranscriptSegmentIndexView(BaseModel):
    """The per-chunk segment discovery read (D12) — unreachable content, only what a
    caller needs to then ask for one segment's turns."""

    chunk_id: str
    segments: list[TranscriptSegmentIndexEntry] = []


class TranscriptSegmentContentView(BaseModel):
    """One segment's decompressed turns, concatenated across its stored records in
    turn-range order — the lazy per-segment content read (D12)."""

    segment_id: str
    final: bool
    truncated: bool
    turns: list[TurnSegmentView] = []
