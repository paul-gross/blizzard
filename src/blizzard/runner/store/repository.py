"""The runner-store repository seam (``bzh:repository-split``/``bzh:dependency-inversion``).

The reconciliation loop reaches its machine-local facts — leases, env bindings, the
outbound buffer, and the P6 lifecycle facts (lease context, closures, releases) —
only through these Protocols. Split read/write: the loop steps hold the write
variant (they are the domain layer, ``bzh:controller-read-only``); a read-only
edge (the ``status`` view) holds the narrow read one. The concrete SQLAlchemy
adapter lives under ``internal/`` and is injected at the composition root.

Facts only, status derived (``bzh:facts-not-status``): an *active* lease is one
with no closure fact; a *held* env is one whose binding has no release fact; a
chunk's *tenure* is live while it holds any unreleased binding. Every timestamp is
passed in by the caller from the injected clock (``bzh:injected-clock``) — the
store never reads a wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class RunnerStoreError(RuntimeError):
    """A runner-store operation failed — the domain-facing error the loop sees.

    Wraps the underlying SQLAlchemy exception at the adapter boundary (logged once
    there), so loop code depends on this type, never on the driver's exceptions.
    """


@dataclass(frozen=True)
class NewLease:
    """A node-step lease at mint — before the worker exists (D-082)."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    runner_id: str
    retries_max: int
    created_at: datetime


@dataclass(frozen=True)
class LeaseRecord:
    """A lease joined with its node context — the loop's per-attempt fact.

    ``pid`` / ``process_start_time`` / ``session_id`` are ``None`` until
    spawn-return records them (D-092).
    """

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    runner_id: str
    retries_max: int
    created_at: datetime
    pid: int | None = None
    process_start_time: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class EnvBindingRecord:
    """A chunk→env binding fact (D-021/D-063)."""

    chunk_id: str
    environment_id: str
    workdir: str
    bound_at: datetime


@dataclass(frozen=True)
class BufferedFact:
    """One pending hub-bound fact in the store-and-forward buffer (D-069)."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    payload: str
    created_at: datetime


@dataclass(frozen=True)
class AskRecord:
    """The worker's local open-ask fact ([ask-answer.md]).

    Recorded by the runner's local API when ``blizzard runner ask`` fires, before the
    worker exits. ``question_id`` is runner-minted so the answer can be polled back by
    it; ``session_id`` is the dormant session the resume-with-answer targets.
    """

    lease_id: str
    chunk_id: str
    question_id: str
    question: str
    options: list[str]
    session_id: str | None
    asked_at: datetime


@dataclass(frozen=True)
class ParkRecord:
    """A lease's park on a question — dormant, no live worker ([ask-answer.md])."""

    lease_id: str
    chunk_id: str
    question_id: str
    parked_at: datetime


class IReadRunnerStore(Protocol):
    """Read-only runner-store queries (held by read-path edges)."""

    def list_active_leases(self) -> list[LeaseRecord]:
        """Leases with no closure fact — the attempts currently in flight."""
        ...

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's single active lease, if any (P6: at most one — MAX_AGENTS math)."""
        ...

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id iff it is still active (no closure fact), else ``None``.

        The flusher drives a buffered completion's apply-response against this: an
        already-closed lease means the completion applied on an earlier flush whose
        ack was lost (D-090) — the re-flush is a no-op past the ack.
        """
        ...

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        REAP's stall signal: a live worker whose last beat is older than the
        conservative staleness threshold has stopped making tool calls (design/
        runner/loop.md). ``None`` falls back to the lease's own creation instant.
        """
        ...

    def pending_completion_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` fact in the buffer.

        ADVANCE reads this to skip a worker whose completion is already buffered so
        the verdict is elicited exactly once while the flush is pending (D-069)."""
        ...

    def held_environment_ids(self) -> list[str]:
        """Every env id whose binding has no release fact (D-062's ``held_ids``)."""
        ...

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        """The chunk's unreleased env bindings (its held environments)."""
        ...

    def live_tenure_chunk_ids(self) -> list[str]:
        """Chunks still held by this runner — those with an unreleased binding (D-083)."""
        ...

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        """How many leases have been minted for this chunk at this node (retry budget)."""
        ...

    def latest_epoch(self, chunk_id: str) -> int:
        """The highest lease epoch minted for this chunk, or 0 — the fence source (D-044)."""
        ...

    def pending_outbound(self) -> list[BufferedFact]:
        """The unacked outbound buffer, FIFO by seq (D-069)."""
        ...

    def unforwarded_ask(self, lease_id: str) -> AskRecord | None:
        """The lease's newest ask not yet parked — its question_id has no park fact.

        ADVANCE parks on this: an exited worker with an unforwarded ask asked and quit
        (ask-and-exit), so the chunk parks rather than being judged (D-009). Once parked
        the park fact references the question_id, so the same ask is not re-parked; a
        resumed worker that asks *again* mints a fresh question_id, returned anew."""
        ...

    def parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question — a park fact with no later resume ([ask-answer.md]).

        REAP skips these (the reap clock is stopped — no live worker to stall) and
        ADVANCE polls them for an answer rather than eliciting a verdict."""
        ...

    def open_park(self, lease_id: str) -> ParkRecord | None:
        """The lease's open park (park fact, no resume), or None — its question_id."""
        ...


class IWriteRunnerStore(IReadRunnerStore, Protocol):
    """Read-write runner store — held only by the domain (the loop steps)."""

    def record_lease(self, lease: NewLease) -> None:
        """Persist a minted lease and its node context, atomically."""
        ...

    def record_spawn(self, lease_id: str, *, pid: int, process_start_time: str, session_id: str) -> None:
        """Fill a lease's spawn-return facts: pid, process start time, session id (D-092)."""
        ...

    def record_binding(self, *, chunk_id: str, environment_id: str, workdir: str, bound_at: datetime) -> None:
        """Persist a chunk→env binding fact (written with the route claim, D-080/D-083)."""
        ...

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        """Append a heartbeat for a lease — a worker tool call fired its hook (D-069)."""
        ...

    def record_closure(self, *, lease_id: str, chunk_id: str, node_id: str, reason: str, closed_at: datetime) -> None:
        """Close a lease — a clean transition or a failure/escalation (D-078)."""
        ...

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        """Release a chunk's env binding at tenure end (D-083)."""
        ...

    def enqueue_outbound(
        self, *, kind: str, chunk_id: str | None, lease_id: str | None, payload: str, created_at: datetime
    ) -> int:
        """Append a hub-bound fact to the store-and-forward buffer; return its seq (D-069)."""
        ...

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Mark a buffered fact delivered — a semantic rejection acks too (D-069)."""
        ...

    def record_ask(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        question_id: str,
        question: str,
        options: list[str],
        session_id: str | None,
        asked_at: datetime,
    ) -> None:
        """Persist the worker's local open-ask fact ([ask-answer.md])."""
        ...

    def record_park(self, *, lease_id: str, chunk_id: str, question_id: str, parked_at: datetime) -> None:
        """Park a lease on a question — dormant, its env bindings held ([ask-answer.md])."""
        ...

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        """End a lease's park — the answer arrived and the session was resumed ([ask-answer.md])."""
        ...
