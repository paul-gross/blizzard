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
    """A node-step lease at mint — before the worker exists."""

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
    spawn-return records them.
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
class ClosedLeaseRecord:
    """A lease joined with its closure fact — the panel's recent-history read (issue #29).

    ``reason`` is the closure vocabulary already written by ``record_closure``
    (``runner/loop/steps.py``): ``transitioned`` | ``reaped`` | ``failed`` | ``escalated``
    | ``parked`` | ``released``.
    """

    lease: LeaseRecord
    reason: str
    closed_at: datetime


@dataclass(frozen=True)
class EnvBindingRecord:
    """A chunk→env binding fact."""

    chunk_id: str
    environment_id: str
    workdir: str
    bound_at: datetime


@dataclass(frozen=True)
class BufferedFact:
    """One pending hub-bound fact in the store-and-forward buffer."""

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
        ack was lost — the re-flush is a no-op past the ack.
        """
        ...

    def lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id, regardless of closure — the transcript read (issue #29).

        Distinct from :meth:`active_lease`, which filters to unclosed leases *by
        design* (the flusher's ack-idempotency check above): a transcript outlives
        its lease, so this read must span closed ones too.
        """
        ...

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        """The most recently closed leases, newest first — the panel's recent-history
        read (issue #29).

        One SQL join (``leases`` ⋈ ``lease_closures``, ordered by ``closed_at`` desc,
        capped at ``limit``) — no N+1. ``limit`` is a **list-length affordance**, not a
        retention policy: it bounds how many rows the panel shows, not how long a
        closure fact or its ``.jsonl`` transcript lives on disk (undecided, out of
        scope). Does not touch :meth:`list_active_leases`, REAP's own read.
        """
        ...

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        REAP's stall signal: a live worker whose last beat is older than the
        conservative staleness threshold has stopped making tool calls (design/
        runner/loop.md). ``None`` falls back to the lease's own creation instant.
        """
        ...

    def pending_submission_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` or ``decision.submitted``
        fact in the buffer.

        ADVANCE reads this to skip a worker whose completion (or runner-config gate
        decision) is already buffered, so the node-step's outcome is elicited exactly
        once while the flush is pending."""
        ...

    def held_environment_ids(self) -> list[str]:
        """Every env id whose binding has no release fact (D-062's ``held_ids``)."""
        ...

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        """The chunk's unreleased env bindings (its held environments)."""
        ...

    def live_tenure_chunk_ids(self) -> list[str]:
        """Chunks still held by this runner — those with an unreleased binding."""
        ...

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        """How many leases have been minted for this chunk at this node (retry budget)."""
        ...

    def latest_epoch(self, chunk_id: str) -> int:
        """The highest lease epoch minted for this chunk, or 0 — the fence source."""
        ...

    def pending_outbound(self) -> list[BufferedFact]:
        """The unacked outbound buffer, FIFO by seq."""
        ...

    def unforwarded_ask(self, lease_id: str) -> AskRecord | None:
        """The lease's newest ask not yet parked — its question_id has no park fact.

        ADVANCE parks on this: an exited worker with an unforwarded ask asked and quit
        (ask-and-exit), so the chunk parks rather than being judged. Once parked
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

    def hub_paused(self, runner_id: str) -> bool:
        """The last hub pause brake PULL mirrored locally — FILL adheres.

        Defaults False when PULL has never synced (a fresh runner claims freely until it
        first hears otherwise)."""
        ...

    def local_paused(self, runner_id: str) -> bool:
        """This runner's own brake, derived from the newest local pause fact (issue #43).

        The runner's half of the pause control (``PATCH /runner``): set locally, adhered
        to with the hub unreachable, and distinct from ``hub_paused``. Since issue #45 it
        blocks every spawn site — FILL's claim, restart-resume, an answer-resume, ADVANCE's
        next-node, a requeue or claim-adopt respawn, and ADVANCE's judgement resume — and
        REAP defers killing a stalled worker and every ``_fail_attempt`` caller defers
        escalating an exhausted budget, so nothing is started and nothing is handed off as
        unrecoverable while paused. The hub brake keeps its claims-only meaning, checked in
        FILL alone. Defaults False when the operator has never set it."""
        ...

    def resume_intent_lease_ids(self) -> set[str]:
        """Leases carrying an **open** restart resume-intent.

        A ``resume_intents`` mark with no ``resume_clears`` for the same lease at or
        after it — set by a graceful shutdown (#12) or ``host``'s startup crash-recovery
        scan (#13), consumed by the startup RESUME step. Empty on any normal tick;
        non-empty only on the first tick after a restart."""
        ...

    def session_ended_lease_ids(self) -> set[str]:
        """Leases whose **current spawn** recorded a session-end — it declared done.

        A ``session_ends`` row means the Claude Code ``SessionEnd`` hook fired on a natural
        session exit. Startup crash-recovery reads this to keep a cleanly-exited worker out
        of the resume path (:func:`mark_crash_resume_intents`): a dead pid *with* a session-end
        is a done declaration ADVANCE judges, not a crash to re-attach.

        Scoped to the lease's newest ``lease_spawns`` fact, because a lease outlives its
        sessions: the ask/answer and resume paths re-spawn under the same lease and session
        id, so an unscoped read would let one natural exit suppress the resume of every
        later crash on that lease — the sessions most worth resuming."""
        ...

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked (issue #13).

        The crash-time reference startup recovery classifies staleness against. The tick
        stamps it each pass, so after an involuntary stop the newest value is when the daemon
        died, to within one tick — letting the scan ask "was this worker still working *when
        the daemon died*" instead of measuring the outage itself."""
        ...

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        """The runtime workspace-prompt override for this workspace, or ``None`` (issue #17).

        ``None`` means never overridden — the spawn preamble falls back to the static
        config prompt. A present row (even an empty string) is a deliberate override that
        wins over config, so an operator can clear the prompt to table-only at runtime."""
        ...


class IWriteRunnerStore(IReadRunnerStore, Protocol):
    """Read-write runner store — held only by the domain (the loop steps)."""

    def record_lease(self, lease: NewLease) -> None:
        """Persist a minted lease and its node context, atomically."""
        ...

    def record_spawn(
        self, lease_id: str, *, pid: int, process_start_time: str, session_id: str, spawned_at: datetime
    ) -> None:
        """Fill a lease's spawn-return facts: pid, process start time, session id.

        ``spawned_at`` additionally appends the lease's spawn generation, so a fact recorded
        by an earlier session of the same lease can be told from one recorded by the process
        running now (issue #13)."""
        ...

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        """Stamp the runner as alive at ``alive_at`` — the tick's liveness beat (issue #13).

        Upserted, one row per runner: only the newest instant matters, and it is the crash-time
        reference startup recovery reads back via :meth:`last_daemon_liveness`."""
        ...

    def record_binding(self, *, chunk_id: str, environment_id: str, workdir: str, bound_at: datetime) -> None:
        """Persist a chunk→env binding fact (written with the route claim, D-080/D-083)."""
        ...

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        """Append a heartbeat for a lease — a worker tool call fired its hook."""
        ...

    def record_closure(self, *, lease_id: str, chunk_id: str, node_id: str, reason: str, closed_at: datetime) -> None:
        """Close a lease — a clean transition or a failure/escalation."""
        ...

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        """Release a chunk's env binding at tenure end."""
        ...

    def enqueue_outbound(
        self, *, kind: str, chunk_id: str | None, lease_id: str | None, payload: str, created_at: datetime
    ) -> int:
        """Append a hub-bound fact to the store-and-forward buffer; return its seq."""
        ...

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Mark a buffered fact delivered — a semantic rejection acks too."""
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

    def set_hub_paused(self, runner_id: str, *, paused: bool, at: datetime) -> None:
        """Mirror the hub's pause brake locally (upsert) — read back by FILL."""
        ...

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, report_kind: str, report_payload: str
    ) -> None:
        """Append a local pause/start fact **and** its hub-bound report, atomically (issue #43).

        Appends rather than upserts because this is a locally-minted fact, not a mirror of
        someone else's value — the same shape as the hub's own pause facts.

        The report is not a separate call by design: a brake the hub is never told about
        leaves the board showing a runner as claiming when it has stopped, and nothing
        reconciles it (PULL only mirrors hub→runner). Taking the buffer entry here is what
        makes the pair crash-atomic — `kill -9` at any instant is a supported operation.
        ``report_kind``/``report_payload`` stay caller-supplied so the store owns no fact
        vocabulary (the same split as :meth:`enqueue_outbound`)."""
        ...

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        """Set the runtime workspace-prompt override (upsert) — read at spawn (issue #17)."""
        ...

    def record_resume_intent(self, *, lease_id: str, marked_at: datetime) -> None:
        """Mark a lease for same-lease restart-resume at graceful shutdown."""
        ...

    def record_resume_clear(self, *, lease_id: str, cleared_at: datetime) -> None:
        """Clear a lease's resume-intent — the RESUME step resumed or abandoned it."""
        ...

    def record_session_end(self, *, lease_id: str, ended_at: datetime) -> None:
        """Record a worker's session-end — the ``SessionEnd`` hook fired on exit."""
        ...
