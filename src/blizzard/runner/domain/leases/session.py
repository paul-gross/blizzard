"""The lease-session repository seam — session-pool head, session-identity lookups,
session-end, and preamble facts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.runner.harness.fingerprint import PreambleFingerprint

if TYPE_CHECKING:
    from blizzard.runner.domain.leases import LeaseRecord, PoolHead


class IReadLeaseSessionRepository(Protocol):
    """Read-only session-pool and session-identity queries (held by read-path edges)."""

    def latest_session_id(self, chunk_id: str, node_name: str | None) -> str | None:
        """The chunk's most-recent session-bearing lease's ``session_id``, or ``None``.

        The newest lease for this chunk whose ``session_id`` is non-null, optionally
        filtered to ``node_name`` (issue #115). ``None`` is the fresh-fallback signal."""
        ...

    def pool_head(self, chunk_id: str, session_name: str) -> PoolHead | None:
        """The named session pool's current head for this chunk, or ``None`` (issue #144).

        The newest session-bearing lease whose ``lease_context.session_name`` matches;
        derived, never a column. **Runner-local**: a chunk reclaimed elsewhere mints fresh.
        """
        ...

    def session_invocation_count(self, session_id: str) -> int:
        """How many harness invocations this session has recorded (issue #144).

        The signal behind a declared ``rotate.max_invocations`` — ``usage_facts`` rows
        across every lease that ran ``session_id``. **Harness invocations, not
        node-steps.** Zero is a real answer here, not an unknown."""
        ...

    def lease_for_session(self, session_id: str) -> LeaseRecord | None:
        """The newest lease that ran ``session_id``, or ``None`` (issue #144).

        Keyed on the *session*, which outlives the lease that minted it: several leases
        share one session id and the newest describes the running configuration."""
        ...

    def session_ended_lease_ids(self) -> set[str]:
        """Leases whose **current spawn** recorded a session-end — it declared done.

        A dead pid *with* a session-end is a done declaration, not a crash to re-attach.
        Scoped to the lease's newest ``lease_spawns`` fact, because a lease outlives its
        sessions and an unscoped read would suppress every later crash's resume."""
        ...

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        """The standing preamble prose this session was last sent, or ``None`` (issue #149).

        The newest ``session_preamble_facts`` row for the session. ``None`` renders the
        full preamble — the safe direction, since an over-eager match would cost the
        worker its updated instructions."""
        ...


class IWriteLeaseSessionRepository(IReadLeaseSessionRepository, Protocol):
    """Read-write session store — held only by the domain (the loop steps)."""

    def record_session_end(self, *, lease_id: str, ended_at: datetime) -> None:
        """Record a worker's session-end — the ``SessionEnd`` hook fired on exit."""
        ...

    def record_session_preamble(self, session_id: str, *, fingerprint: PreambleFingerprint, at: datetime) -> None:
        """Record what standing preamble prose this session was just sent (issue #149).

        Append-only; the newest row is what the fingerprint read returns. The fact is
        *"this prose was sent to this session"*, not *"a spawn happened"*, and is written
        after the spawn so a durable fingerprint implies the prose reached the process."""
        ...
