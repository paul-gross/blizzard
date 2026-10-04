"""The transcript route's domain read model — resolves a lease's transcript to
a home. Holds only read-only seams (``bzh:repository-split``),
so a controller may hold it directly (``bzh:controller-read-only``). ``leases.lease(lease_id)``
spans closure — unlike ``active_lease`` — because a transcript outlives its lease. Local until
acked, hub after (:meth:`TranscriptService.for_lease`); the runner-plane's
chunk-scoped segment reads resolve locally too, through that same session-file read."""

from __future__ import annotations

from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.leases import IReadLeaseRecordRepository, Lease
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.home import (
    ResolvedSegmentContent,
    ResolvedTranscript,
    home_is_local,
    segment_window,
    session_start_cursor,
)
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, TranscriptSegmentState
from blizzard.runner.transcripts.repository import ITranscriptRepositoryResolver, Transcript

__all__ = ["ResolvedSegmentContent", "ResolvedTranscript", "TranscriptService"]


class TranscriptService:
    """Resolves a lease id to its transcript, via its home-selection table —
    ``None`` iff no such lease ever existed."""

    def __init__(
        self,
        leases: IReadLeaseRecordRepository,
        transcript_ledger: IReadTranscriptLedgerRepository,
        environments: IReadEnvironmentRepository,
        transcripts: ITranscriptRepositoryResolver,
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
            # session yet, on either side.
            return ResolvedTranscript.local(Transcript.spawning())
        # Resolve even when the archived copy may answer: the persisted owner governs this
        # concrete session's transcript, and an archive must not conceal an absent owner.
        session = lease.session
        assert session is not None
        self._transcripts.transcript_repository(session.harness_id)

        # The unshipped-content probe runs only for a closed lease, as the short-circuit reads it.
        lease_active = self._leases.active_lease(lease_id) is not None
        if home_is_local(
            lease_active=lease_active,
            unshipped=not lease_active and self._transcript_ledger.has_unshipped_transcript_content(lease.chunk_id),
        ):
            return ResolvedTranscript.local(self._read_local(lease))

        # Closed and fully acked: the hub is the home, the file its fallback.
        archived = self._archived.read_turns(chunk_id=lease.chunk_id, node_id=lease.node_id, epoch=lease.epoch)
        resolved = ResolvedTranscript.from_archive(lease.session_id, archived)
        if resolved is not None:
            return resolved
        return ResolvedTranscript.local_fallback(self._read_local(lease), archived)

    def segments_for_chunk(self, chunk_id: str) -> list[TranscriptSegmentState]:
        """The chunk's segment ledger rows, straight off the store — open or
        finalized, superseded or not. A chunk this store holds no lease for returns
        ``[]``, which is also this method's whole ownership-exclusion behavior:
        the store never wrote another runner's segments in the first place."""
        return self._transcript_ledger.transcript_segments_for_chunk(chunk_id)

    def segment_content(self, chunk_id: str, segment_id: str) -> ResolvedSegmentContent | None:
        """One segment's content, resolved through its session file — ``None`` iff no such
        segment exists under this chunk on this store (404, mirroring :meth:`for_lease`). Windowed
        to this segment's own turns: starts where the preceding sibling left off, ends at this
        segment's own frozen cursor once finalized — never a sibling's still-advancing one."""
        segment = self._transcript_ledger.transcript_segment(segment_id)
        if segment is None or segment.chunk_id != chunk_id:
            return None
        start_cursor = session_start_cursor(segment, self._transcript_ledger.transcript_segments_for_chunk(chunk_id))
        spawn_cwd = self._spawn_cwd(chunk_id)
        local = self._read_local_session(segment.session, spawn_cwd=spawn_cwd, since=start_cursor)
        tail = None
        if local.available and segment.final and segment.cursor is not None:
            tail = self._read_local_session(segment.session, spawn_cwd=spawn_cwd, since=segment.cursor)
        return segment_window(segment, local, tail)

    def _read_local(self, lease: Lease) -> Transcript:
        assert lease.session_id is not None
        session = lease.session
        assert session is not None
        return self._read_local_session(session, spawn_cwd=self._spawn_cwd(lease.chunk_id))

    def _read_local_session(
        self, session: SessionReference, *, spawn_cwd: str | None, since: str | None = None
    ) -> Transcript:
        repository = self._transcripts.transcript_repository(session.harness_id)
        return repository.read_turns(session.session_id, spawn_cwd=spawn_cwd, since=since)

    def _spawn_cwd(self, chunk_id: str) -> str | None:
        bindings = self._environments.bindings_for_chunk(chunk_id)
        # A closed lease's bindings are already released, so `bindings_for_chunk` returns `[]` and the
        # hint is legitimately `None`; the primary by-session-id lookup does not need it.
        fallback_workdir = bindings[0].workdir if bindings else None
        return SpawnCwd(self._workspace_root, fallback_workdir).path
