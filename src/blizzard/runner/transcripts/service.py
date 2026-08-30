"""The transcript route's domain read model (issue #29) — resolves a lease's transcript to
a home per Decision 1 (blizzard#249). Holds only read-only seams (``bzh:repository-split``),
so a controller may hold it directly (``bzh:controller-read-only``). ``store.lease(lease_id)``
spans closure — unlike ``active_lease`` — because a transcript outlives its lease.
Local until acked, hub after (issue #249 AC1): see :meth:`TranscriptService.for_lease`."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.store.repository import IReadRunnerStore
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript, TranscriptProvenance


@dataclass(frozen=True)
class ResolvedTranscript:
    """A lease's transcript, resolved to a home per Decision 1. ``provenance`` and
    ``hub_unreachable`` carry straight onto their wire-field namesakes
    (``wire.transcript.TranscriptResponse``, the fields' own doc home)."""

    transcript: Transcript
    provenance: TranscriptProvenance
    hub_unreachable: bool


class TranscriptService:
    """Resolves a lease id to its transcript, per Decision 1's home-selection table —
    ``None`` iff no such lease ever existed."""

    def __init__(
        self,
        store: IReadRunnerStore,
        transcripts: IReadTranscriptRepository,
        archived: IReadArchivedTranscriptRepository,
        workspace_root: str,
    ) -> None:
        self._store = store
        self._transcripts = transcripts
        self._archived = archived
        self._workspace_root = workspace_root

    def for_lease(self, lease_id: str) -> ResolvedTranscript | None:
        """The lease's resolved transcript, or ``None`` when no lease with this id ever
        existed — never for a lease that exists but has no session yet or no transcript
        anywhere, which are ``ResolvedTranscript(transcript=Transcript(available=False, …))``."""
        lease = self._store.lease(lease_id)
        if lease is None:
            return None
        if lease.session_id is None:
            # Minted at FILL, spawn-return not yet recorded — the agent has not started a
            # session yet, on either side. Ordinary, not an error.
            transcript = Transcript(session_id=None, available=False, reason="spawning", turns=[], truncated=False)
            return ResolvedTranscript(transcript=transcript, provenance="local", hub_unreachable=False)

        if self._store.active_lease(lease_id) is not None or self._store.has_unshipped_transcript_content(
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

    def _read_local(self, lease: LeaseRecord) -> Transcript:
        assert lease.session_id is not None
        bindings = self._store.bindings_for_chunk(lease.chunk_id)
        # A closed lease's bindings are already released, so `bindings_for_chunk` returns `[]` and the
        # hint is legitimately `None`; the primary by-session-id lookup does not need it.
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = SpawnCwd(self._workspace_root, fallback_workdir).path
        return self._transcripts.read_turns(lease.session_id, spawn_cwd=spawn_cwd)
