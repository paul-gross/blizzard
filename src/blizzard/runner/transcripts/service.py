"""The transcript route's domain read model (issue #29) — resolves a lease's transcript to
a home per Decision 1 (blizzard#249). Holds only read-only seams (``bzh:repository-split``),
so a controller may hold it directly (``bzh:controller-read-only``). ``leases.lease(lease_id)``
spans closure — unlike ``active_lease`` — because a transcript outlives its lease. Local until
acked, hub after (issue #249 AC1, :meth:`TranscriptService.for_lease`); the runner-plane's
chunk-scoped segment reads (D1/D4) resolve locally too, through that same session-file read."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.domain.leases import IReadLeaseRepository, LeaseRecord
from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, TranscriptSegmentLedgerRow
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript, TranscriptProvenance, Turn


@dataclass(frozen=True)
class ResolvedTranscript:
    """A lease's transcript, resolved to a home per Decision 1. ``provenance`` and
    ``hub_unreachable`` carry straight onto their wire-field namesakes
    (``wire.transcript.TranscriptResponse``, the fields' own doc home)."""

    transcript: Transcript
    provenance: TranscriptProvenance
    hub_unreachable: bool


@dataclass(frozen=True)
class ResolvedSegmentContent:
    """One segment's resolved content, read straight from its session file (D1) — never
    from the ledger's own shipped-turn accounting, which only bounds the index read (D6).
    ``turns`` is ``[]`` with ``available=False`` when the session file is gone; a caller
    renders that as ``truncated=True, turns=[]`` (D2's wire model has no unavailability field)."""

    final: bool
    available: bool
    truncated: bool
    turns: list[Turn]


class TranscriptService:
    """Resolves a lease id to its transcript, per Decision 1's home-selection table —
    ``None`` iff no such lease ever existed."""

    def __init__(
        self,
        leases: IReadLeaseRepository,
        transcript_ledger: IReadTranscriptLedgerRepository,
        environments: IReadEnvironmentRepository,
        transcripts: IReadTranscriptRepository,
        archived: IReadArchivedTranscriptRepository,
        workspace_root: str,
    ) -> None:
        self._leases = leases
        self._transcript_ledger = transcript_ledger
        self._environments = environments
        self._transcripts = transcripts
        self._archived = archived
        self._workspace_root = workspace_root

    def for_lease(self, lease_id: str) -> ResolvedTranscript | None:
        """The lease's resolved transcript, or ``None`` when no lease with this id ever
        existed — never for a lease that exists but has no session yet or no transcript
        anywhere, which are ``ResolvedTranscript(transcript=Transcript(available=False, …))``."""
        lease = self._leases.lease(lease_id)
        if lease is None:
            return None
        if lease.session_id is None:
            # Minted at FILL, spawn-return not yet recorded — the agent has not started a
            # session yet, on either side. Ordinary, not an error.
            transcript = Transcript(session_id=None, available=False, reason="spawning", turns=[], truncated=False)
            return ResolvedTranscript(transcript=transcript, provenance="local", hub_unreachable=False)

        if self._leases.active_lease(lease_id) is not None or self._transcript_ledger.has_unshipped_transcript_content(
            lease.chunk_id
        ):
            # Local until acked (AC1): an open lease, and a closed one whose chunk still
            # holds unshipped turns — the hub's copy would be a prefix of the file's.
            local = self._read_local(lease)
            return ResolvedTranscript(transcript=local, provenance="local", hub_unreachable=False)

        # Closed and fully acked: the hub is the home (D1). A refusal, an empty index, and
        # a turn-less "found" all fall back to local exactly alike.
        archived = self._archived.read_turns(chunk_id=lease.chunk_id, node_id=lease.node_id, epoch=lease.epoch)
        if archived.status == "found" and archived.turns:
            transcript = Transcript(
                session_id=lease.session_id,
                available=True,
                reason=None,
                turns=archived.turns,
                truncated=archived.truncated,
            )
            return ResolvedTranscript(transcript=transcript, provenance="archived", hub_unreachable=False)

        local = self._read_local(lease)
        # Only a *not_found* local read becomes the hub-unreachable state: `unreadable` has
        # its own panel row, and masking that fault behind "we couldn't ask" hides it.
        hub_unreachable = archived.status == "unreachable" and local.reason == "not_found"
        return ResolvedTranscript(transcript=local, provenance="local", hub_unreachable=hub_unreachable)

    def segments_for_chunk(self, chunk_id: str) -> list[TranscriptSegmentLedgerRow]:
        """The chunk's segment ledger rows, straight off the store (D6) — open or
        finalized, superseded or not. A chunk this store holds no lease for returns
        ``[]``, which is also this method's whole ownership-exclusion behavior (D3):
        the store never wrote another runner's segments in the first place."""
        return self._transcript_ledger.transcript_segments_for_chunk(chunk_id)

    def segment_content(self, chunk_id: str, segment_id: str) -> ResolvedSegmentContent | None:
        """One segment's content, resolved through its session file (D1) — ``None`` iff
        no such segment exists under this chunk on this store, which the route renders as
        404 (mirroring :meth:`for_lease`'s "never existed" convention)."""
        segment = self._transcript_ledger.transcript_segment(segment_id)
        if segment is None or segment.chunk_id != chunk_id:
            return None
        local = self._transcripts.read_turns(segment.session_id, spawn_cwd=self._spawn_cwd(segment.chunk_id))
        final = segment.finalized_at is not None
        if not local.available:
            return ResolvedSegmentContent(final=final, available=False, truncated=True, turns=[])
        truncated = local.truncated or segment.truncated_reason is not None
        return ResolvedSegmentContent(final=final, available=True, truncated=truncated, turns=local.turns)

    def _read_local(self, lease: LeaseRecord) -> Transcript:
        assert lease.session_id is not None
        return self._transcripts.read_turns(lease.session_id, spawn_cwd=self._spawn_cwd(lease.chunk_id))

    def _spawn_cwd(self, chunk_id: str) -> str | None:
        bindings = self._environments.bindings_for_chunk(chunk_id)
        # A closed lease's bindings are already released, so `bindings_for_chunk` returns `[]` and the
        # hint is legitimately `None`; the primary by-session-id lookup does not need it.
        fallback_workdir = bindings[0].workdir if bindings else None
        return SpawnCwd(self._workspace_root, fallback_workdir).path
