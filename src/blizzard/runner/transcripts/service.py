"""The transcript route's domain read model (issue #29).

Holds only :class:`~blizzard.runner.store.repository.IReadRunnerStore` (the read-only
variant, ``bzh:repository-split``) and :class:`~blizzard.runner.transcripts.repository
.IReadTranscriptRepository` — a read path, so it is safe for a controller to hold
this service directly (``bzh:controller-read-only``), mirroring
:class:`blizzard.runner.domain.leases.LocalLeaseService`.

``store.lease(lease_id)`` **spans closure** — distinct from
``active_lease``, which filters to unclosed leases by design — because a transcript
outlives its lease: the panel lists recently-closed leases beside active
ones, and a closed lease's transcript must still be readable.
"""

from __future__ import annotations

from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
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
        """The lease's transcript, or ``None`` when no lease with this id ever existed.

        ``None`` is the route's 404 signal — never returned for a lease that
        exists but simply has no session yet or no transcript on disk; those are
        ``Transcript(available=False, reason=…)``, a normal 200.
        """
        lease = self._store.lease(lease_id)
        if lease is None:
            return None
        if lease.session_id is None:
            # Minted at FILL, spawn-return not yet recorded — the agent has
            # not started a session yet. Ordinary, not an error.
            return Transcript(session_id=None, available=False, reason="spawning", turns=[], truncated=False)

        bindings = self._store.bindings_for_chunk(lease.chunk_id)
        # A closed lease's bindings are already released (LeaseActivity owns this
        # invariant, domain/leases.py) — `bindings_for_chunk` then returns `[]` and
        # the hint is legitimately `None`. The glob-by-session-id primary lookup
        # does not need it.
        fallback_workdir = bindings[0].workdir if bindings else None
        spawn_cwd = resolve_spawn_cwd(self._workspace_root, fallback_workdir)
        return self._transcripts.read_turns(lease.session_id, spawn_cwd=spawn_cwd)
