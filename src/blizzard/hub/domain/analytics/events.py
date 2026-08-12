"""The transcript-event store seam and its domain types (blizzard#254, Phase 1).

An event row is an immutable observation, never a status (``bzh:facts-not-status``): it
is fully re-derivable from the segments that back it, nothing derives a status from it,
and its source's mutability is bounded and *observed* rather than assumed — the
derivation marker records what a derivation saw, so the sweep can tell a segment's
stored content changed underneath an earlier pass."""

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
class SegmentDerivationInput:
    """Everything a segment offers the derivation service (Phase 3): decoded once,
    fingerprinted once. ``complete`` is ``False`` when any of the segment's records is a
    content hole (D6's rejected-record case) — the source over which ``turns`` was
    decoded is then a partial view, never silently indistinguishable from a session that
    read nothing."""

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

    def visible_segment_ids(self) -> frozenset[str]:
        """Every segment id the hub's own read path would show today (D1) — final, not
        superseded. The derivation service's candidate set is this, filtered against
        :meth:`derivation_marker`; the reconciler diffs it against
        :meth:`derived_segment_ids` to find a segment to drop."""
        ...

    def derived_segment_ids(self) -> frozenset[str]:
        """Every segment id carrying at least one derivation marker, at any extractor
        version — the reconciler's own bookkeeping of what it has ever derived."""
        ...

    def segment_derivation_input(self, segment_id: str) -> SegmentDerivationInput | None:
        """``segment_id``'s decoded turns and content fingerprint, or ``None`` when the
        segment no longer exists at all (superseded segments still resolve; only the
        caller's own visible-set check decides whether to derive)."""
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

    def drop_segment(self, segment_id: str) -> None:
        """One transaction: delete every row and marker this segment ever produced, at
        every extractor version — the reconciler's own response to a segment leaving the
        visible set (D1, D6)."""
        ...
