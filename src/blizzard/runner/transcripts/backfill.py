"""The backfill's and the re-ship's decisions — pure, over loaded segments and plain values.

:mod:`~blizzard.runner.transcripts.transcript_backfill` reads the ledger, probes the harness,
and carries these out: which lease a pass imports, which segments are left unfinished, and
whether — and over which segment — a re-ship opens a new one."""

from __future__ import annotations

from collections.abc import Iterable, Set
from dataclasses import dataclass
from typing import Literal

from blizzard.foundation.roles import dto
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.transcripts.ledger import TranscriptBackfillLease, TranscriptSegmentState

__all__ = [
    "MERGED_GENERATION",
    "BackfillVerdict",
    "ReshipRefused",
    "SegmentOpening",
    "TranscriptReshipError",
    "classify_backfill",
    "newest_superseder",
    "require_reshippable",
    "resumable_for",
    "unfinished",
]

#: A merged import claims the lease's first spawn: pre-epic sessions recorded no resume
#: offsets, so an in-place-resumed session has no seam to split a later generation at.
MERGED_GENERATION = 1


class TranscriptReshipError(Exception):
    """A re-ship that cannot be attempted at all. A run that came back PARTIAL is not one
    of these — that outcome is the report's own ``complete``."""


class ReshipRefused(TranscriptReshipError):
    """The re-ship rule refuses this source as it stands — a live lease, an unfinished
    segment, or a spent chunk budget."""


#: How one pass treats one session-bearing lease: already segmented, unreadable, held back, or imported.
BackfillVerdict = Literal["already_present", "gone", "deferred", "import"]


def classify_backfill(
    lease: TranscriptBackfillLease,
    *,
    readable: bool,
    imported: int,
    limit: int | None,
    backpressured: bool,
) -> BackfillVerdict:
    """Classify one not-yet-seen lease, in order: a session already holding a segment is
    present; an unreadable one is gone (nothing written, so a rerun retries it); past
    ``limit`` imports, or with the buffer backpressured, it is deferred; else imported."""
    if lease.has_segment:
        return "already_present"
    if not readable:
        return "gone"
    if limit is not None and imported >= limit:
        return "deferred"
    if backpressured:
        return "deferred"
    return "import"


def unfinished(
    open_segments: Iterable[TranscriptSegmentState], active_lease_ids: Set[str]
) -> list[TranscriptSegmentState]:
    """Open segments on an already-closed lease — an interrupted earlier run's own. A live
    lease's segment belongs to the tick's pump, never the backfill."""
    return [segment for segment in open_segments if segment.lease_id not in active_lease_ids]


def resumable_for(
    target: TranscriptSegmentState, unfinished_segments: Iterable[TranscriptSegmentState]
) -> TranscriptSegmentState | None:
    """An earlier re-ship's own still-open segment for ``target``'s session, if one was left
    behind — resumed rather than stranded beside a second. Never ``target`` itself."""
    return next(
        (s for s in unfinished_segments if s.session == target.session and s.segment_id != target.segment_id),
        None,
    )


def newest_superseder(
    source: TranscriptSegmentState, segments: Iterable[TranscriptSegmentState]
) -> TranscriptSegmentState:
    """The newest finalized segment in ``source``'s supersession chain — ``source`` itself
    when nothing finalized supersedes it. A re-ship supersedes this one, so the chain stays
    linear: no segment is ever superseded twice by a re-ship."""
    finalized = [s for s in segments if s.final and s.supersedes is not None]
    current = source
    while True:
        successors = [s for s in finalized if s.supersedes == current.segment_id]
        if not successors:
            return current
        current = max(successors, key=lambda s: s.stamped_at)


@dto
@dataclass(frozen=True)
class SegmentOpening:
    """The coordinates a new segment opens at, before its ``stamped_at``."""

    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    lease_id: str
    session: SessionReference
    supersedes: str | None
    spawn_cwd: str | None

    @classmethod
    def superseding(cls, target: TranscriptSegmentState) -> SegmentOpening:
        """A re-ship's segment: ``target``'s own lease coordinates, pointed at what it replaces.
        Without that pointer the hub's lease read — keyed on the lease, not the segment —
        concatenates both and renders the conversation twice."""
        return cls(
            chunk_id=target.chunk_id,
            node_id=target.node_id,
            epoch=target.epoch,
            generation=target.generation,
            lease_id=target.lease_id,
            session=target.session,
            supersedes=target.segment_id,
            spawn_cwd=target.spawn_cwd,
        )

    @classmethod
    def merged_import(cls, lease: TranscriptBackfillLease, *, spawn_cwd: str | None) -> SegmentOpening:
        """A backfill import's segment: the lease's first spawn, superseding nothing."""
        return cls(
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            generation=MERGED_GENERATION,
            lease_id=lease.lease_id,
            session=lease.session,
            supersedes=None,
            spawn_cwd=spawn_cwd,
        )


def require_reshippable(
    source: TranscriptSegmentState,
    *,
    lease_active: bool,
    chunk_shipped_bytes: int,
    chunk_max_bytes: int,
) -> None:
    """Refuse a re-ship of ``source`` (raising :class:`ReshipRefused`), in order: its lease is
    still active, so its segment belongs to the running pump; it is unfinished, so the backfill
    owes it its end first; or the chunk's transcript budget is already spent, so a new segment
    could ship nothing."""
    if lease_active:
        raise ReshipRefused(
            f"lease {source.lease_id} is still active — its segment belongs to the running "
            "pump; re-ship it once the lease closes"
        )
    if not source.final:
        raise ReshipRefused(
            f"segment {source.segment_id} is unfinished — finish it first: blizzard runner transcript backfill"
        )
    if chunk_shipped_bytes >= chunk_max_bytes:
        raise ReshipRefused(
            f"chunk {source.chunk_id} has spent its transcript budget "
            f"({chunk_shipped_bytes} of {chunk_max_bytes} bytes) — a re-ship would ship nothing"
        )
