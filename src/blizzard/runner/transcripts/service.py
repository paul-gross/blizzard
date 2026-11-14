"""The transcript read model — resolves a lease's transcript to its home
(``src/blizzard/runner/transcripts/home.py``). Holds only read-only seams (``bzh:repository-split``).
``leases.lease(lease_id)`` spans closure — unlike ``active_lease`` — because a transcript outlives
its lease. Chunk-scoped segment reads resolve through the same session-file read."""

from __future__ import annotations

from collections.abc import Callable

from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.environments.workspaces import IWorkspaceSpawnRoots
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.home import (
    ResolvedSegmentContent,
    ResolvedTranscript,
    home_is_local,
    segment_window,
    session_start_cursor,
)
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository, TranscriptSegmentState
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, ITranscriptRepositoryResolver, Transcript

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
        workspaces: IWorkspaceSpawnRoots,
        transcripts_for_lease: Callable[[str], ITranscriptRepositoryResolver] | None = None,
    ) -> None:
        self._leases = leases
        self._transcript_ledger = transcript_ledger
        self._environments = environments
        self._transcripts = transcripts
        self._transcripts_for_lease = transcripts_for_lease
        self._archived = archived
        self._workspaces = workspaces

    def for_lease(self, lease_id: str) -> ResolvedTranscript | None:
        """The lease's resolved transcript, or ``None`` when no lease with this id ever
        existed — never for a lease that exists but has no session yet or no transcript
        anywhere, which are ``ResolvedTranscript(transcript=Transcript(available=False, …))``."""
        lease = self._leases.lease(lease_id)
        if lease is None:
            return None
        if lease.session_id is None:
            return ResolvedTranscript.local(Transcript.spawning())
        # Resolve even when the archived copy may answer: the persisted owner governs this
        # concrete session's transcript, and an archive must not conceal an absent owner.
        session = lease.session
        assert session is not None
        transcripts = self._resolver(lease_id)
        repository = transcripts.transcript_repository(session.harness_id)

        lease_active = self._leases.active_lease(lease_id) is not None
        if home_is_local(
            lease_active=lease_active,
            unshipped=not lease_active and self._transcript_ledger.has_unshipped_transcript_content(lease.chunk_id),
        ):
            return ResolvedTranscript.local(self._read_local(lease, repository))

        # The non-local home: src/blizzard/runner/transcripts/home.py.
        archived = self._archived.read_turns(chunk_id=lease.chunk_id, node_id=lease.node_id, epoch=lease.epoch)
        resolved = ResolvedTranscript.from_archive(lease.session_id, archived)
        if resolved is not None:
            return resolved
        return ResolvedTranscript.local_fallback(self._read_local(lease, repository), archived)

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
        spawn_cwd = segment.spawn_cwd if segment.spawn_cwd is not None else self.spawn_cwd(chunk_id)
        repository = self._resolver(segment.lease_id).transcript_repository(segment.session.harness_id)
        local = repository.read_turns(segment.session.session_id, spawn_cwd=spawn_cwd, since=start_cursor)
        tail = None
        if local.available and segment.final and segment.cursor is not None:
            tail = repository.read_turns(segment.session.session_id, spawn_cwd=spawn_cwd, since=segment.cursor)
        return segment_window(segment, local, tail)

    def _read_local(self, lease: Lease, repository: IReadTranscriptRepository) -> Transcript:
        assert lease.session_id is not None
        session = lease.session
        assert session is not None
        return repository.read_turns(session.session_id, spawn_cwd=self.spawn_cwd(lease.chunk_id))

    def _resolver(self, lease_id: str) -> ITranscriptRepositoryResolver:
        return self._transcripts_for_lease(lease_id) if self._transcripts_for_lease else self._transcripts

    def spawn_cwd(self, chunk_id: str) -> str | None:
        # The newest binding, released or not: a closed lease's transcript still reads in its own workspace. A
        # chunk never bound here has no hint, which the primary by-session-id lookup does not need.
        binding = self._environments.newest_binding_for_chunk(chunk_id)
        if binding is None:
            return None
        return SpawnCwd(self._workspaces.spawn_root(binding.workspace_id), binding.workdir).path
