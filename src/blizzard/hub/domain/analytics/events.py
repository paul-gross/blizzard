"""The transcript-event store seam and its domain types (blizzard#254).

An event row is an immutable observation, never a status (``bzh:facts-not-status``): it
is fully re-derivable from the segments that back it, and its source's mutability is
bounded and *observed*, not assumed — the derivation marker records what a derivation
saw, so the sweep can tell a segment's stored content changed since."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.wire.transcript_segment import TurnSegmentView

#: :attr:`TranscriptEvent.kind` values this build's extractors mint (D5) — open to a
#: future extractor registering a new one; no column or migration gates a new entry.
KIND_FILE_READ = "file_read"
KIND_SKILL_INVOCATION = "skill_invocation"
KIND_AGENT_SPAWN = "agent_spawn"


@dataclass(frozen=True)
class TranscriptEvent:
    """One derived occurrence, ready to store. ``segment_id``/``extractor_version`` are
    not carried here — they are the same for every event in one
    :meth:`IWriteTranscriptEvents.replace_segment_events` call, so that method takes them
    once rather than every row repeating them."""

    kind: str
    turn_path: str
    occurrence: int
    payload: str  # JSON object text (D5, `bzh:sql-portable` — never a JSON column type)
    subject: str | None  # denormalized projection (blizzard#255 D1) — filterable, e.g. path prefix
    tool: str | None  # the invoking tool name (blizzard#255 D1) — filterable
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    graph_id: str
    depth: int
    agent_type: str | None
    occurred_at: datetime | None


@dataclass(frozen=True)
class DerivationMarker:
    """One ``(segment_id, extractor_version)`` pair's most recent derivation (D6)."""

    segment_id: str
    extractor_version: str
    content_fingerprint: str
    derived_at: datetime
    event_count: int
    complete: bool


@dataclass(frozen=True)
class CandidacyRead:
    """One candidacy pass's whole visibility evaluation (blizzard#513 D2): the visible
    segment set, and which of those segments' stored digests disagree with their current-
    version marker (or carry none at all). The reconciler's drop pass reuses
    ``visible_segment_ids`` rather than evaluating it a second time."""

    visible_segment_ids: frozenset[str]
    candidate_segment_ids: list[str]


@dataclass(frozen=True)
class DerivationSignature:
    """A cheap aggregate fingerprint of every input :meth:`IReadTranscriptEvents.candidacy`
    and its visibility read see today (blizzard#524 D5): row count, highest
    ``transcript_segments.id``, latest ``received_at``, and ``chunks`` row count. No
    per-row content is read; the reconciler compares this against the previous pass's signature."""

    segment_count: int
    max_segment_id: int | None
    max_received_at: datetime | None
    chunk_count: int


@dataclass(frozen=True)
class SegmentDerivationInput:
    """Everything a segment offers the derivation service: decoded once,
    fingerprinted once. ``complete`` is ``False`` when a record is a content hole (D6) —
    ``turns`` is then a partial view, declared rather than indistinguishable from a
    session that read nothing."""

    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    normalizer_version: str
    turns: list[TurnSegmentView]
    complete: bool
    content_fingerprint: str


class IReadTranscriptEvents(Protocol):
    """Read-only operations over the derived event store and its derivation markers."""

    def visible_segment_ids(self, *, chunk_id: str | None = None) -> frozenset[str]:
        """Every segment id the hub's own read path would show today (D1) — final, not
        superseded, and pointing at a chunk that exists — narrowed to ``chunk_id`` when given
        (the re-derive route's chunk-scoped call, D7)."""
        ...

    def derived_segment_ids(self) -> frozenset[str]:
        """Every segment id carrying at least one derivation marker, at any extractor
        version — the reconciler's own bookkeeping of what it has ever derived."""
        ...

    def candidacy(self, extractor_version: str, *, chunk_id: str | None = None) -> CandidacyRead:
        """The pass's one visibility evaluation (D2): a bulk, constant-statement-count read
        of the visible set's stored digests against their current-version markers — no
        content byte is read and no statement runs per segment. A segment absent from the
        visible set is never a candidate even with no marker at all."""
        ...

    def derivation_signature(self) -> DerivationSignature:
        """The standing reconciler's change probe (blizzard#524 D5): one constant-cost
        aggregate read over every input :meth:`candidacy` and its visibility read see —
        no per-row read, so its cost never grows with segment count. The reconciler
        compares this against the previous pass's signature to decide whether to skip the
        full pass (candidacy/derive/drop) entirely."""
        ...

    def segment_derivation_input(self, segment_id: str) -> SegmentDerivationInput | None:
        """``segment_id``'s decoded turns and content fingerprint, or ``None`` when the
        segment no longer exists at all (superseded segments still resolve; only the
        caller's own visible-set check decides whether to derive). Called only by
        :meth:`~blizzard.hub.domain.analytics.derivation.EventDerivationService.derive_segment`
        (D2) — :meth:`candidacy` never decodes content, so this is the sweep's one decode."""
        ...

    def derivation_marker(self, segment_id: str, extractor_version: str) -> DerivationMarker | None: ...


class IWriteTranscriptEvents(IReadTranscriptEvents, Protocol):
    """Read-write variant. Only :class:`~blizzard.hub.domain.analytics.derivation.EventDerivationService`
    depends on this."""

    def replace_segment_events(
        self,
        segment_id: str,
        extractor_version: str,
        events: list[TranscriptEvent],
        *,
        complete: bool,
        content_fingerprint: str,
        at: datetime,
    ) -> None:
        """One transaction: delete this pair's existing rows, write ``events``, and write
        the marker (D6). Rows at *other* extractor versions are untouched."""
        ...

    def drop_segments(self, segment_ids: frozenset[str]) -> None:
        """One transaction: delete every row and marker every one of ``segment_ids`` ever
        produced, at every extractor version, set-scoped rather than one transaction per
        segment (D4) — the reconciler's own response to segments leaving the visible set
        (D1, D6). A no-op for an empty set."""
        ...
