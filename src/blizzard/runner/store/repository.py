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
class OutboundFactRecord:
    """One hub-bound fact off the outbound buffer, acked or not — the local fact log's row.

    :class:`BufferedFact` is the *pending* tail DRAIN posts; this is the same table read as
    a ledger (``acked_at`` kept, ``payload`` dropped — log readers want the event, not the body)."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: datetime
    acked_at: datetime | None


@dataclass(frozen=True)
class AskRecord:
    """The worker's local open-ask fact.

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
    """A lease's park on a question — dormant, no live worker."""

    lease_id: str
    chunk_id: str
    question_id: str
    parked_at: datetime


@dataclass(frozen=True)
class EscalationRecord:
    """A closed-``escalated`` lease not yet superseded — the status view's read (issue #51).

    An escalation is recorded by closing its lease with ``reason="escalated"``
    (``runner/loop/steps.py``'s ``_escalate``); the chunk's environments stay bound
    for a human takeover. It stays *open* until a later lease is minted for the same
    chunk (a requeue) — the highest ``epoch`` for the chunk still being this one's is
    exactly that "no later mint" fact (``bzh:facts-not-status``), so no separate
    resolution flag is stored. ``session_id`` is the dormant session a resume command
    is built around; ``None`` only if the escalated lease never reached spawn-return."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    session_id: str | None
    closed_at: datetime


@dataclass(frozen=True)
class TakeoverRecord:
    """An open operator takeover — the human-in-session fact (issue #52).

    ``lease_id``/``session_id`` name the lease and session the interactive command
    resumes; ``lease_id`` is ``None`` for the needs_human and gate-parked shapes, whose
    lease already closed before the takeover was opened. ``fence_epoch`` is set only
    when a live worker was force-killed — the epoch reported to the hub so the killed
    worker's in-flight completion is fenced as stale."""

    takeover_id: str
    chunk_id: str
    lease_id: str | None
    session_id: str | None
    workdir: str
    fence_epoch: int | None
    opened_at: datetime


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

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's most-recently-minted lease, active or closed (issue #52).

        Unlike :meth:`active_lease_for_chunk`, spans closed leases too — the
        needs_human (escalated) and gate-parked (``reason="parked"``) shapes have no
        active lease by the time a takeover is requested, but their closed lease still
        carries the session id a takeover resumes."""
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
        conservative staleness threshold has stopped making tool calls. ``None``
        falls back to the lease's own creation instant.
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
        """Every env id whose binding has no release fact (the provider's ``held_ids``)."""
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

    def recent_outbound(self, limit: int) -> list[OutboundFactRecord]:
        """The newest ``limit`` outbound facts, acked or not, newest first — the local fact log."""
        ...

    def unforwarded_ask(self, lease_id: str) -> AskRecord | None:
        """The lease's newest ask not yet parked — its question_id has no park fact.

        ADVANCE parks on this: an exited worker with an unforwarded ask asked and quit
        (ask-and-exit), so the chunk parks rather than being judged. Once parked
        the park fact references the question_id, so the same ask is not re-parked; a
        resumed worker that asks *again* mints a fresh question_id, returned anew."""
        ...

    def parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question **or an operator pause** — the union of
        :meth:`ask_parked_lease_ids` and :meth:`pause_parked_lease_ids` (issue #46).

        REAP skips these (the reap clock is stopped — no live worker to stall) and
        ADVANCE polls the ask-parked ones for an answer rather than eliciting a
        verdict; every existing skip site inherits pause-parks through this union
        with no other change ([ask-answer.md])."""
        ...

    def ask_parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question — a park fact with no later resume ([ask-answer.md]).

        The ask-park half of :meth:`parked_lease_ids`'s union."""
        ...

    def pause_parked_lease_ids(self) -> set[str]:
        """Leases dormant on an operator pause — a pause-park fact with no later
        pause-resume at or after it (issue #46).

        The pause-park half of :meth:`parked_lease_ids`'s union; ADVANCE's routing
        discriminator between an ask-park and a pause-park."""
        ...

    def open_park(self, lease_id: str) -> ParkRecord | None:
        """The lease's open park (park fact, no resume), or None — its question_id."""
        ...

    def open_asks(self) -> list[AskRecord]:
        """Every ask with no answer yet — forwarded-and-parked or still unforwarded (issue #51).

        The status view's read: an ask is open while its ``question_id`` carries no
        :meth:`record_park_resume`, whether or not it has been forwarded up via
        :meth:`record_park` yet — mirrors :meth:`unforwarded_ask`'s and
        :meth:`open_park`'s per-lease reads, widened to every lease."""
        ...

    def held_bindings(self) -> list[EnvBindingRecord]:
        """Every currently-held env binding, across every chunk (issue #51).

        The status view's read — :meth:`bindings_for_chunk` widened from one chunk to
        the whole fleet this runner holds, the same ``held`` predicate
        :meth:`held_environment_ids` and :meth:`live_tenure_chunk_ids` already use."""
        ...

    def open_escalations(self) -> list[EscalationRecord]:
        """Every escalated chunk not yet superseded by a later lease mint (issue #51).

        The status view's read of :class:`EscalationRecord` — see its docstring for
        what "open" means here."""
        ...

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        """The chunk's open escalation, or ``None`` (issue #53).

        The single-chunk narrowing of :meth:`open_escalations` — :class:`RequeueService`'s
        needs_human guard: a closed-``escalated`` lease not yet superseded by a later mint.
        Unaffected by a takeover opening or ending over the chunk in between — a takeover
        never writes a closure or mints a lease, so this reads the same whether or not one
        happened, covering both the pasted-command and the ended-takeover requeue flows with
        the one predicate."""
        ...

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        """When PULL last **successfully** reached the hub, or ``None`` if never (issue #51).

        :meth:`set_hub_paused` is only called after a successful
        ``register_runner``/``fetch_runner_paused`` round trip
        (``runner/loop/steps.py``'s ``_sync_registry``), so its ``updated_at`` **is**
        the last-successful-contact instant — no separate fact needed
        (``bzh:facts-not-status``). The status view derives reachability from how stale
        this reads against ``now``."""
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

    def open_takeover_for_chunk(self, chunk_id: str) -> TakeoverRecord | None:
        """The chunk's open takeover, or ``None`` — a ``takeovers`` row with no
        ``takeover_ends`` row for the same ``takeover_id`` (issue #52).

        At most one open takeover per chunk by construction: ``TakeoverService`` refuses
        a second ``POST`` while one is already open."""
        ...

    def open_takeover_chunk_ids(self) -> set[str]:
        """Every chunk id currently under an open takeover (issue #52).

        The loop's per-tick skip set: REAP and ADVANCE read this once per phase so no
        step touches a chunk's session while the human holds it."""
        ...

    def open_takeovers(self) -> list[TakeoverRecord]:
        """Every open takeover, across every chunk (issue #51).

        The status view's recovery surface: a takeover left open by a stranded CLI
        (e.g. an interrupted terminal that never reached the end-PATCH) would
        otherwise wedge its chunk with no visible way to find the ``takeover_id`` back
        — this is the read that names it, alongside the chunk and how long it has been
        held, mirroring :meth:`open_escalations`'s widened-to-the-fleet shape."""
        ...

    def pending_requeue_chunk_ids(self) -> set[str]:
        """Every chunk id carrying a requeue mark not yet consumed by a later lease mint
        (issue #53).

        FILL's own hoisted-once read (mirroring ``pause_parked_lease_ids``'s convention):
        :func:`~blizzard.runner.loop.steps._reconcile_interrupted_claims` spawns a fresh
        attempt for each — the ordinary :func:`~blizzard.runner.loop.steps._spawn_attempt`
        mint that follows both consumes the mark (its ``created_at`` lands at or after the
        requeue) and, via its outbound ``lease.minted`` fact, supersedes the escalation at
        the hub."""
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
        """Persist a chunk→env binding fact (written with the route claim)."""
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
        """Persist the worker's local open-ask fact."""
        ...

    def record_park(self, *, lease_id: str, chunk_id: str, question_id: str, parked_at: datetime) -> None:
        """Park a lease on a question — dormant, its env bindings held."""
        ...

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        """End a lease's park — the answer arrived and the session was resumed."""
        ...

    def record_pause_park(self, *, lease_id: str, chunk_id: str, parked_at: datetime) -> None:
        """Park a lease on an operator pause — dormant, its env bindings held (issue #46)."""
        ...

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        """End a lease's pause-park — the operator resumed it (issue #46)."""
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

    def record_takeover(
        self,
        *,
        takeover_id: str,
        chunk_id: str,
        lease_id: str | None,
        session_id: str | None,
        workdir: str,
        fence_epoch: int | None,
        opened_at: datetime,
    ) -> None:
        """Open a takeover — recorded before any kill and before the interactive command
        is returned (issue #52), so no later tick can race the human for the chunk."""
        ...

    def record_takeover_end(self, *, takeover_id: str, ended_at: datetime) -> None:
        """Close a takeover — the CLI calls this once its exec'd interactive child exits."""
        ...

    def record_requeue(self, *, chunk_id: str, at: datetime) -> None:
        """Append the clearing fact for a chunk's local needs_human hold (issue #53).

        Recorded before anything else runs (``bzh:crash-correctness``): the fact alone is
        durable the instant this returns, and the next FILL's
        :func:`~blizzard.runner.loop.steps._reconcile_interrupted_claims` reads it back via
        :meth:`pending_requeue_chunk_ids` to spawn the fresh attempt — this call never spawns
        anything itself."""
        ...
