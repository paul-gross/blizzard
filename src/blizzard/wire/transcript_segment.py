"""Transcript segment wire bodies (``epic:transcripts``) — the first wire
projection of #245's normalized turn model onto shipped, hub-stored content.

A record is one shipped **turn-range slice** of a segment; ``seq`` is the lane's
high-water sequence, ``(segment_id, turn_range_start)`` the re-offer dedupe key."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.foundation.transcripts import TurnKind


class ToolCallSegmentView(BaseModel):
    """A tool invocation, structured: what was called, with what input, and what came back."""

    name: str
    input: dict[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool
    # Optional so a row missing this field still validates.
    input_truncated: bool = False
    #: This turn carries ONLY a result for the call `tool_use_id` names, shipped in an earlier
    #: window — a patch onto that earlier call, not new content of its own.
    output_patch: bool = False


class SidechainSegmentView(BaseModel):
    """A subagent's private conversation, nested under the tool call that spawned it, or
    carried on its own ``sidechain`` turn when no spawning call resolved. Recursive: a
    sidechain turn may itself carry a tool call whose own sidechain nests further."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[TurnSegmentView]
    #: The call that spawned this conversation, when it shipped in an earlier window than the
    #: conversation did — an id, never an index, which a lease read renumbers.
    parent_tool_use_id: str | None = None


class TurnSegmentView(BaseModel):
    """One normalized turn, carried in full. ``index`` is **segment-relative** and producer-minted,
    stable across a segment's batches — EXCEPT under ``sidechain.turns``, where it restarts at 0 within
    that one sidechain, and on a lease transcript read, where it numbers only the turns that read
    returned and slides with the recency window. ``kind`` is closed."""

    index: int
    #: Closed to :data:`TurnKind` — an out-of-vocabulary kind is a hard failure, never a
    #: silent round-trip (`docs/versioning.md`).
    kind: TurnKind
    timestamp: str | None
    text: str
    tool: ToolCallSegmentView | None
    thinking_redacted: bool
    sidechain: SidechainSegmentView | None
    truncated: bool


SidechainSegmentView.model_rebuild()


class TranscriptSegmentRecord(BaseModel):
    """One shipped turn-range slice of a segment. ``final=True`` marks the one record
    that closes the segment out. ``record_truncated`` is the runner's own declaration that
    THIS record lost content it would otherwise carry — shrunk, an incomplete source read,
    or (only when neither closes the gap) ``turns`` emptied — distinct from ``rejected``."""

    seq: int
    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    # Optional for previous-minor runners; the producing family, distinct from its version.
    harness_id: str | None = None
    normalizer_version: str
    harness_version: str | None
    # Optional for previous-minor runners, exactly as harness_id is.
    model: str | None = None
    effort: str | None = None
    # Optional for previous-minor runners; the worker's working directory, frozen at the segment's open.
    spawn_cwd: str | None = None
    record_truncated: bool = False
    #: Re-ship only: the segment this replaces, which the lease key alone cannot distinguish.
    supersedes: str | None = None
    turns: list[TurnSegmentView]


class TranscriptSegmentBatch(BaseModel):
    """A runner's push of one-or-more buffered transcript records, ordered by ``seq`` —
    the transcript lane's own store-and-forward batch, distinct from the fact lane's
    ``RunnerFactBatch``."""

    runner_id: str
    records: list[TranscriptSegmentRecord]


class TranscriptSegmentAck(BaseModel):
    """The hub's per-batch acknowledgement against the transcript lane's high-water mark.

    ``capped`` is the cap-rejection class — acknowledged, content-dropped, and the
    high-water advances past it, a durable decision that must not re-adjudicate on replay."""

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    capped: list[int] = []


class TranscriptSegmentIndexEntry(BaseModel):
    """One segment's metadata row — byte counts and completion state, never turn
    content. ``truncated`` is true iff any record was cap-rejected OR the runner
    itself declared ``record_truncated`` on one, so a consumer can tell an incomplete
    segment from a short one without fetching it."""

    segment_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    truncated: bool
    byte_count: int
    harness_id: str | None = None
    normalizer_version: str
    harness_version: str | None
    received_at: str


class TranscriptSegmentIndexView(BaseModel):
    """The per-chunk segment discovery read — unreachable content, only what a
    caller needs to then ask for one segment's turns."""

    chunk_id: str
    segments: list[TranscriptSegmentIndexEntry] = []


class TranscriptSegmentContentView(BaseModel):
    """One segment's decompressed turns, concatenated across its stored records in
    turn-range order — the lazy per-segment content read."""

    segment_id: str
    final: bool
    truncated: bool
    turns: list[TurnSegmentView] = []


class LeaseTranscriptView(BaseModel):
    """A lease's transcript, concatenated across every segment stored under its
    ``(chunk_id, node_id, epoch)`` — every spawn generation, not one. The fleet-plane
    counterpart to the per-segment content read: a runner's read-back of its own shipped
    segments. No ``final``: a segment's own closes only *that* segment."""

    chunk_id: str
    node_id: str
    epoch: int
    truncated: bool
    turns: list[TurnSegmentView] = []
