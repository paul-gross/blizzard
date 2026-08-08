"""The transcript route's domain read model (issue #29).

Holds only the read-only store and transcript repositories (``bzh:repository-split``), so a controller
may hold this service directly (``bzh:controller-read-only``). ``store.lease(lease_id)`` **spans
closure** — unlike ``active_lease``, which filters to unclosed leases — because a transcript outlives
its lease and a closed lease's transcript must stay readable."""

from __future__ import annotations

from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.store.repository import IReadRunnerStore
from blizzard.runner.transcripts.repository import IReadTranscriptRepository, Transcript


class LocalTranscriptService:
    """Resolves a lease id to its parsed transcript — ``None`` iff no such lease ever existed."""

    def __init__(
        self,
        store: IReadRunnerStore,
        transcripts: IReadTranscriptRepository,
        workspace_root: str,
    ) -> None:
        self._store = store
        self._transcripts = transcripts
        self._workspace_root = workspace_root

    def for_lease(self, lease_id: str) -> Transcript | None:
        """The lease's transcript, or ``None`` when no lease with this id ever existed — never for a
        lease that exists but has no session yet or no transcript on disk, which are
        ``Transcript(available=False, reason=…)``."""
        lease = self._store.lease(lease_id)
        if lease is None:
            return None
        if lease.session_id is None:
            # Minted at FILL, spawn-return not yet recorded — the agent has
            # not started a session yet. Ordinary, not an error.
            return Transcript(session_id=None, available=False, reason="spawning", turns=[], truncated=False)

        bindings = self._store.bindings_for_chunk(lease.chunk_id)
        # A closed lease's bindings are already released, so `bindings_for_chunk` returns `[]` and the
        # hint is legitimately `None`; the primary by-session-id lookup does not need it.
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = SpawnCwd(self._workspace_root, fallback_workdir).path
        return self._transcripts.read_turns(lease.session_id, spawn_cwd=spawn_cwd)
