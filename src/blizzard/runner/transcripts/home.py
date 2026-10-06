"""Where a transcript is read from, and which window of a session file a segment owns — pure,
over loaded leases, segments, and reads.

*Local until acked, hub after*: an open lease, or a closed one whose chunk still holds unshipped turns,
reads its session file; a closed, fully acked one reads the hub's archived copy, falling back to the
file. :class:`~blizzard.runner.transcripts.service.TranscriptService` does the reads."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.foundation.transcripts import TranscriptProvenance
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript
from blizzard.runner.transcripts.ledger import TranscriptSegmentState
from blizzard.runner.transcripts.repository import Transcript, Turn

__all__ = [
    "ResolvedSegmentContent",
    "ResolvedTranscript",
    "home_is_local",
    "segment_window",
    "session_start_cursor",
]


def home_is_local(*, lease_active: bool, unshipped: bool) -> bool:
    """Whether a lease's transcript is read locally: while its lease is open, or while its
    chunk still holds unshipped turns — the hub's copy would be a prefix of the file's."""
    return lease_active or unshipped


@domain_model
@dataclass(frozen=True)
class ResolvedTranscript:
    """A lease's transcript, resolved to a home: which home answered (``provenance``) and
    whether the hub was unreachable when it was consulted (``hub_unreachable``)."""

    transcript: Transcript
    provenance: TranscriptProvenance
    hub_unreachable: bool

    @classmethod
    def local(cls, transcript: Transcript) -> ResolvedTranscript:
        return cls(transcript=transcript, provenance="local", hub_unreachable=False)

    @classmethod
    def from_archive(cls, session_id: str, archived: ArchivedTranscript) -> ResolvedTranscript | None:
        """The hub's archived copy as the answer — only a ``found`` read holding turns; a
        refusal, an empty index, and a turn-less ``found`` all fall back to local alike."""
        if archived.status != "found" or not archived.turns:
            return None
        transcript = Transcript(
            session_id=session_id, available=True, reason=None, turns=archived.turns, truncated=archived.truncated
        )
        return cls(transcript=transcript, provenance="archived", hub_unreachable=False)

    @classmethod
    def local_fallback(cls, local: Transcript, archived: ArchivedTranscript) -> ResolvedTranscript:
        """The file's read after the archive did not answer. Only a *not_found* local read
        becomes the hub-unreachable state: ``unreadable`` has its own panel row, and masking
        that fault behind "we couldn't ask" hides it."""
        hub_unreachable = archived.status == "unreachable" and local.reason == "not_found"
        return cls(transcript=local, provenance="local", hub_unreachable=hub_unreachable)


def session_start_cursor(
    segment: TranscriptSegmentState, chunk_segments: Iterable[TranscriptSegmentState]
) -> str | None:
    """This segment's own read start within its session file — a same-session resume chains
    several segments over one file, so the window starts where the chronologically preceding
    sibling left off; the first segment of its session has no start bound. ``chunk_segments``
    is the chunk's ledger, oldest first."""
    siblings = [s for s in chunk_segments if s.session == segment.session]
    index = next(i for i, s in enumerate(siblings) if s.segment_id == segment.segment_id)
    return siblings[index - 1].cursor if index > 0 else None


@domain_model
@dataclass(frozen=True)
class ResolvedSegmentContent:
    """One segment's resolved content, read straight from its session file — never
    from the ledger's own shipped-turn accounting, which only bounds the index read.
    ``turns`` is ``[]`` with ``available=False`` when the session file is gone."""

    final: bool
    available: bool
    truncated: bool
    turns: list[Turn]


def segment_window(
    segment: TranscriptSegmentState, from_start: Transcript, tail: Transcript | None
) -> ResolvedSegmentContent:
    """Window a segment's content: ``from_start`` is its session read from its own start,
    ``tail`` the read past its frozen final cursor (``None`` while it is open, or with no
    cursor). A finalized segment drops that tail — a later sibling's turns, never its own.
    Truncated by the read itself, the tail's read, or the segment's own loss (:attr:`truncated`)."""
    if not from_start.available:
        return ResolvedSegmentContent(final=segment.final, available=False, truncated=True, turns=[])
    turns = from_start.turns
    truncated = from_start.truncated or segment.truncated
    if tail is not None and tail.available:
        turns = turns[: max(0, len(turns) - len(tail.turns))]
        truncated = truncated or tail.truncated
    return ResolvedSegmentContent(final=segment.final, available=True, truncated=truncated, turns=turns)
