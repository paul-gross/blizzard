"""The runner-store repository seam (``bzh:repository-split``/``bzh:dependency-inversion``).

Machine-local facts — leases, env bindings, the outbound buffer, and the P6
lifecycle facts (lease context, closures, releases) — are reached only through these
Protocols. Split read/write: the domain layer holds the write variant
(``bzh:controller-read-only``), read-path edges the narrow read one. The concrete
SQLAlchemy adapter lives under ``internal/`` and is injected at the composition root.

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

from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.usage import UsageSample


class RunnerStoreError(RuntimeError):
    """A runner-store operation failed — the domain-facing error the loop sees.

    Wraps the underlying driver exception at the adapter boundary, so loop code
    depends on this type, never on the driver's exceptions.
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
    # What session this attempt runs, and under what configuration (issue #144) — the
    # declared pool name, and the model/effort the session ACTUALLY runs under. Stamped
    # on the one `lease_context` insert the mint already performs, so no new crash
    # window opens. `None` means *unknown*, never a value: the bare/`resume:<node>`
    # forms belong to no pool, and a lease minted before #144 reads NULL.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None


@dataclass(frozen=True)
class PoolHead:
    """A named session pool's current head (issue #144) — what a ``resume:<name>``
    member continues and what a rotation check measures.

    ``resolved_model``/``resolved_effort`` are the head's own **stamps** — the
    configuration that session actually ran under, not a fresh resolution. ``None`` on
    either means *unknown*, never a value.
    """

    session_id: str
    lease_id: str
    resolved_model: str | None
    resolved_effort: str | None


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
    # This attempt's session stamps, read back (issue #144) — see :class:`NewLease`, which
    # writes them. `None` on any of the three means *unknown*, never a value.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None
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

    The same table as :class:`BufferedFact`, read as a ledger rather than as the pending
    tail: ``acked_at`` kept, ``payload`` dropped."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: datetime
    acked_at: datetime | None


@dataclass(frozen=True)
class AskRecord:
    """The worker's local open-ask fact.

    ``question_id`` is runner-minted so the answer can be polled back by it;
    ``session_id`` is the dormant session the resume-with-answer targets.
    """

    lease_id: str
    chunk_id: str
    question_id: str
    question: str
    options: list[str]
    session_id: str | None
    asked_at: datetime


@dataclass(frozen=True)
class UsageTotals:
    """A summed window of usage facts — the runner-ceiling read
    (:meth:`IReadRunnerStore.usage_since`, issue #58).

    ``cost_partial`` carries the lower-bound + PARTIAL contract on ``cost_usd``, whose
    canonical statement is :class:`~blizzard.hub.domain.work.UsageTotal`: a caller must
    check ``cost_partial`` before treating ``cost_usd`` as exact."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


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

    An escalation is a lease closed with ``reason="escalated"`` (``runner/loop/steps.py``).
    It stays *open* until a later lease is minted for the same chunk (a requeue) — the
    highest ``epoch`` for the chunk still being this one's is exactly that "no later mint"
    fact (``bzh:facts-not-status``), so no separate resolution flag is stored.
    ``session_id`` is the dormant session a resume command is built around; ``None`` only
    if the escalated lease never reached spawn-return.

    ``session_name``/``resolved_model``/``resolved_effort`` are the escalated lease's own
    stamps (issue #144) — what the parked session actually ran under, not a fresh
    resolution. ``None`` on any of them means *unknown*, or, for ``session_name``, a
    session on the bare vocabulary that belongs to no pool."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    session_id: str | None
    closed_at: datetime
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None


@dataclass(frozen=True)
class GitCommitDeclarationRecord:
    """A worker's explicit git-commit declaration for one repo in one environment.

    Carries no forge: the origin a declaration is verified against is read from the
    environment's repo manifest at collection time, so it is a fact about the workspace
    rather than a claim the worker makes.

    ``environment_id`` is part of the identity, not a decoration: a chunk holding several
    environments has a worktree of the same repo in each, so ``repo`` alone names a
    branch ambiguously."""

    environment_id: str
    repo: str
    branch: str
    commit: str


@dataclass(frozen=True)
class CheckResultRecord:
    """One check command's runner-executed outcome, read back from the durable store
    (issue #114). ``output_tail`` is runner-local evidence and never rides the wire."""

    command: str
    passed: bool
    output_tail: str


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

        The flusher's ack-idempotency check: an already-closed lease means the completion
        applied on an earlier flush whose ack was lost.
        """
        ...

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        """The chunk's most-recently-minted lease, active or closed (issue #52).

        Unlike :meth:`active_lease_for_chunk`, spans closed leases too: a takeover can be
        requested with no active lease left, and the closed one still carries the session
        id it resumes."""
        ...

    def latest_session_id(self, chunk_id: str, node_name: str | None) -> str | None:
        """The chunk's most-recent session-bearing lease's ``session_id``, or ``None``.

        The newest lease (by mint order) for this chunk whose ``session_id`` is non-null,
        optionally filtered to leases minted at ``node_name`` — any node when ``node_name``
        is ``None`` (issue #115). ``None`` when no such lease exists is the fresh-fallback
        signal: the caller spawns fresh rather than resuming."""
        ...

    def pool_head(self, chunk_id: str, session_name: str) -> PoolHead | None:
        """The named session pool's current head for this chunk, or ``None`` (issue #144).

        The newest session-bearing lease for ``chunk_id`` whose ``lease_context.session_name``
        matches. ``None`` (an empty pool) is the mint-fresh signal.

        Derived, never a ``pool_head`` column (``bzh:facts-not-status``): the head is
        whichever lease most recently stamped this name.

        The pool is **runner-local**, the same limitation :meth:`latest_session_id` has: a
        chunk reclaimed by a second runner sees an empty pool and mints fresh.

        Filtered to session-bearing leases because ``leases.session_id`` is filled at
        spawn-*return*: a crash between the mint and that return leaves a lease that never
        ran, which must not become a head no session exists for.
        """
        ...

    def session_context_tokens(self, session_id: str) -> int | None:
        """The session's **latest invocation's** context size in tokens, or ``None``.

        The signal behind a declared ``rotate.max_context_tokens`` (issue #144):
        ``cache_read + cache_create + input`` on the newest ``usage_facts`` row for any
        lease that ran ``session_id`` — an approximation of how much context the next
        resume would re-ingest.

        ``usage_facts`` carries no ``session_id`` of its own, so this joins through
        ``leases.session_id``; a session spanning several leases (``--resume`` reuses the
        id in place) is measured across all of them, newest row wins.

        **Telemetry-derived**, and ``None`` when the session has no usage fact at all —
        an *unknown*, never a zero.
        """
        ...

    def session_invocation_count(self, session_id: str) -> int:
        """How many harness invocations this session has recorded (issue #144).

        The signal behind a declared ``rotate.max_invocations``. Counts ``usage_facts``
        rows across every lease that ran ``session_id``.

        **Harness invocations, not node-steps**: ``kind`` spans ``spawn|resume|judge|nudge``,
        so a single node-step burns two or three rows.

        Telemetry-derived like :meth:`session_context_tokens`: an invocation that recorded
        no usage fact is not counted. Zero is a real answer here, not an unknown.
        """
        ...

    def lease_for_session(self, session_id: str) -> LeaseRecord | None:
        """The newest lease that ran ``session_id``, or ``None`` (issue #144).

        Keyed on the *session* rather than the lease, because a session outlives the lease
        that minted it: `--resume` reuses the id in place, so several leases share one
        session id and the newest is the one whose stamps describe the configuration the
        process is running under now.

        ``None`` — a session this runner never minted a lease for — means *unknown*.
        """
        ...

    def lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id, regardless of closure — the transcript read (issue #29).

        Distinct from :meth:`active_lease`, which filters to unclosed leases: a
        transcript outlives its lease, so this read must span closed ones too.
        """
        ...

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        """The most recently closed leases, newest first — the panel's recent-history
        read (issue #29).

        ``limit`` is a **list-length affordance**, not a retention policy: it bounds how
        many rows are returned, not how long a closure fact lives on disk.
        """
        ...

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        REAP's stall signal; on ``None`` the caller falls back to :meth:`latest_spawn`,
        then to the lease's own creation instant.
        """
        ...

    def latest_spawn(self, lease_id: str) -> datetime | None:
        """When this lease's newest process was spawned, or ``None`` if it never was.

        The second half of REAP's staleness baseline (issue #150). A lease outlives its
        processes — the ask/answer, pause, restart and crash resume paths all re-spawn
        under the same ``lease_id`` — and each ``record_spawn`` appends a
        ``lease_spawns`` row, so the newest one is when the *currently running* worker
        actually started.

        ``None`` for a lease whose spawn-return never landed; the baseline then falls back
        to the lease's own ``created_at``.
        """
        ...

    def pending_submission_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` or ``decision.submitted``
        fact in the buffer.

        ADVANCE's skip set, so a node-step's outcome is elicited exactly once while the
        flush is pending."""
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

        Once parked, the park fact references the question_id, so the same ask is not
        re-parked; a resumed worker that asks *again* mints a fresh question_id,
        returned anew."""
        ...

    def parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question **or an operator pause** — the union of
        :meth:`ask_parked_lease_ids` and :meth:`pause_parked_lease_ids` (issue #46).

        A parked lease has no live worker, so REAP's stall clock does not apply
        ([ask-answer.md])."""
        ...

    def ask_parked_lease_ids(self) -> set[str]:
        """Leases dormant on a question — a park fact with no later resume ([ask-answer.md]).

        The ask-park half of :meth:`parked_lease_ids`'s union."""
        ...

    def pause_parked_lease_ids(self) -> set[str]:
        """Leases dormant on an operator pause — a pause-park fact with no later
        pause-resume at or after it (issue #46).

        The pause-park half of :meth:`parked_lease_ids`'s union."""
        ...

    def open_park(self, lease_id: str) -> ParkRecord | None:
        """The lease's open park (park fact, no resume), or None — its question_id."""
        ...

    def open_asks(self) -> list[AskRecord]:
        """Every ask with no answer yet — forwarded-and-parked or still unforwarded (issue #51).

        An ask is open while its ``question_id`` carries no :meth:`record_park_resume`,
        whether or not it has been forwarded up via :meth:`record_park` yet —
        :meth:`unforwarded_ask`'s and :meth:`open_park`'s per-lease reads widened to
        every lease."""
        ...

    def held_bindings(self) -> list[EnvBindingRecord]:
        """Every currently-held env binding, across every chunk (issue #51).

        :meth:`bindings_for_chunk` widened from one chunk to the whole fleet this runner
        holds, on the same ``held`` predicate."""
        ...

    def open_escalations(self) -> list[EscalationRecord]:
        """Every escalated chunk not yet superseded by a later lease mint (issue #51).

        See :class:`EscalationRecord` for what "open" means here."""
        ...

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        """The chunk's open escalation, or ``None`` (issue #53).

        The single-chunk narrowing of :meth:`open_escalations`: a closed-``escalated``
        lease not yet superseded by a later mint. Unaffected by a takeover opening or
        ending over the chunk in between — a takeover writes neither a closure nor a
        lease mint."""
        ...

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        """When PULL last **successfully** reached the hub, or ``None`` if never (issue #51).

        :meth:`set_hub_paused` is only called after a successful hub round trip
        (``runner/loop/steps.py``), so its ``updated_at`` **is** the last-successful-
        contact instant — no separate fact needed (``bzh:facts-not-status``)."""
        ...

    def hub_paused(self, runner_id: str) -> bool:
        """The last hub pause brake PULL mirrored locally — FILL adheres.

        Defaults False when PULL has never synced (a fresh runner claims freely until it
        first hears otherwise)."""
        ...

    def local_paused(self, runner_id: str) -> bool:
        """This runner's own brake, derived from the newest local pause fact (issue #43).

        The runner's half of the pause control (``PATCH /runner``): set locally, adhered
        to with the hub unreachable, and distinct from ``hub_paused`` — it blocks every
        spawn site, not claims alone (issue #45). Defaults False when the operator has
        never set it."""
        ...

    def resume_intent_lease_ids(self) -> set[str]:
        """Leases carrying an **open** restart resume-intent.

        A ``resume_intents`` mark with no ``resume_clears`` for the same lease at or
        after it (#12, #13). Empty on any normal tick; non-empty only on the first tick
        after a restart."""
        ...

    def session_ended_lease_ids(self) -> set[str]:
        """Leases whose **current spawn** recorded a session-end — it declared done.

        A ``session_ends`` row means the harness's session-end hook fired on a natural
        session exit — a dead pid *with* a session-end is a done declaration, not a crash
        to re-attach (:func:`mark_crash_resume_intents`).

        Scoped to the lease's newest ``lease_spawns`` fact, because a lease outlives its
        sessions: the ask/answer and resume paths re-spawn under the same lease and session
        id, so an unscoped read would let one natural exit suppress the resume of every
        later crash on that lease — the sessions most worth resuming."""
        ...

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked (issue #13).

        The crash-time reference startup recovery classifies staleness against. The tick
        stamps it each pass, so after an involuntary stop the newest value is when the
        daemon died, to within one tick."""
        ...

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        """The runtime workspace-prompt override for this workspace, or ``None`` (issue #17).

        ``None`` means never overridden — the caller falls back to the static config
        prompt. A present row (even an empty string) is a deliberate override that wins
        over config."""
        ...

    def route_token(self, chunk_id: str) -> str | None:
        """The chunk's stashed route capability token, or ``None`` if never claimed here
        (issue #84a). Stamped onto every chunk-scoped outbound payload at enqueue.
        ``None`` is presented as an absent field, never fabricated."""
        ...

    def lease_token_hash(self, lease_id: str) -> str | None:
        """The lease's minted capability token hash, or ``None`` if never minted
        here (issue #113, Phase 1) — what an attach authorization check compares a
        presented plaintext's hash against."""
        ...

    def open_takeover_for_chunk(self, chunk_id: str) -> TakeoverRecord | None:
        """The chunk's open takeover, or ``None`` — a ``takeovers`` row with no
        ``takeover_ends`` row for the same ``takeover_id`` (issue #52).

        At most one open takeover per chunk by construction: ``TakeoverService`` refuses
        a second ``POST`` while one is already open."""
        ...

    def open_takeover_chunk_ids(self) -> set[str]:
        """Every chunk id currently under an open takeover (issue #52).

        The loop's per-tick skip set, so no step touches a chunk's session while the
        human holds it."""
        ...

    def open_takeovers(self) -> list[TakeoverRecord]:
        """Every open takeover, across every chunk (issue #51).

        :meth:`open_takeover_for_chunk` widened to the fleet, mirroring
        :meth:`open_escalations`'s shape — the read that names a takeover left open by a
        stranded CLI, which would otherwise wedge its chunk."""
        ...

    def pending_requeue_chunk_ids(self) -> set[str]:
        """Every chunk id carrying a requeue mark not yet consumed by a later lease mint
        (issue #53).

        FILL's own hoisted-once read (mirroring ``pause_parked_lease_ids``'s convention).
        The mark is consumed by the next lease mint for the chunk, whose ``created_at``
        lands at or after the requeue."""
        ...

    def lease_generation(self, lease_id: str) -> int:
        """This lease's current spawn generation — the count of its ``lease_spawns`` rows
        (issue #58): 1 at the initial spawn, incrementing at each resume that calls
        ``record_spawn`` again under this lease. Usage's idempotency co-key
        (:meth:`IWriteRunnerStore.record_usage`) and its kind discriminator — generation 1
        is a ``spawn``, every later generation a ``resume``."""
        ...

    def lease_ids_for_chunk(self, chunk_id: str) -> list[str]:
        """Every lease id ever minted for this chunk, active or closed (issue #58).

        A chunk's tenure can span several node-steps and retries, each its own lease —
        this is the release-time read that finds every one of them, not just the
        currently-active lease."""
        ...

    def usage_since(self, at: datetime) -> UsageTotals:
        """Sum every local usage fact recorded at or after ``at`` (issue #58) — see
        :class:`UsageTotals` for the lower-bound + PARTIAL contract on ``cost_usd``."""
        ...

    def last_external_usage_attempt_at(self) -> datetime | None:
        """The derived cadence anchor for the external-subscription-usage sample step
        (issue #218): ``max(sampled_at)`` across ``external_usage_samples``, or ``None``
        if this runner has never attempted a sample.

        Derived rather than a separately-stored "last sampled" column
        (``bzh:facts-not-status``). Counts a NULL-``payload`` attempt (the harness had
        nothing to report) exactly like a successful one: either way, this runner *tried*
        at that instant."""
        ...

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        """The lease's explicit artifact submissions, newest content per ``name``
        (issue #113, Phase 2). Append-only, latest-wins-per-``(lease_id, name)``: a
        worker's re-attach of the same name (a correction) reads back as the
        replacement, never a duplicate. Empty for a lease that never attached
        anything."""
        ...

    def git_commit_declarations_for_lease(self, lease_id: str) -> dict[tuple[str, str], GitCommitDeclarationRecord]:
        """The lease's explicit git-commit declarations, newest per ``(environment_id,
        repo)`` (issue #143, Phase 3), keyed the same way.

        Append-only, latest-wins: a worker's re-declaration of the same repo in the same
        environment (a correction) reads back as the replacement, never a duplicate.
        Keying on the environment as well as the repo is what keeps a chunk holding
        several environments from collapsing one env's branch onto another's.

        Empty for a lease that never declared a commit."""
        ...

    def nudge_fired(self, lease_id: str, epoch: int) -> bool:
        """``True`` iff this attempt's `produces`-unmet nudge is already spent
        (issue #113, Phase 4) — the durable guard consulted before resuming a worker
        session to nudge it. Written by
        :meth:`~IWriteRunnerStore.record_nudge_fired` *before* that resume runs, so a
        crash between the two still leaves this reading ``True`` on the next pass."""
        ...

    def checks_ran(self, lease_id: str, epoch: int) -> bool:
        """``True`` iff this attempt's ``checks:`` have already run and their results are
        durable (issue #114) — the guard consulted before running a node's checks. Written by
        :meth:`~IWriteRunnerStore.record_checks_ran` *after* the result rows, so this
        reading ``True`` implies the rows exist (``runner:checks-recorded-when-marked``).
        A crash after the rows but before the marker leaves this ``False`` on recovery,
        which safely re-runs (latest-wins). Never set for a node with no ``checks:``."""
        ...

    def check_results_for_lease(self, lease_id: str, epoch: int) -> list[CheckResultRecord]:
        """This attempt's recorded check results, in run order (issue #114). Empty for an
        attempt whose checks never ran (or a node with no ``checks:``)."""
        ...

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        """The standing preamble prose this session was last sent, or ``None`` (issue #149).

        The newest ``session_preamble_facts`` row for the session, read only when a spawn
        resumes one.

        ``None`` means "nothing recorded for this session" and renders the full three-layer
        preamble. That is the safe direction — a missing fingerprint costs tokens, an
        over-eager match would cost the worker its updated instructions."""
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

    def record_closure(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        reason: str,
        closed_at: datetime,
        event_kind: str | None = None,
        event_payload: str | None = None,
    ) -> None:
        """Close a lease — a clean transition or a failure/escalation.

        When ``event_kind``/``event_payload`` are given (issue #125), the operational
        event they carry is enqueued to the outbound buffer **in the same transaction** as
        the closure — the ``record_local_pause`` atomic-pairing precedent — so the event
        and the closure it describes land together or not at all."""
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

        Taking the buffer entry here rather than leaving the report to a separate call is
        what makes the brake and its report crash-atomic (pinned by
        tests/test_ingest_and_pause_verbs.py::test_pause_reports_itself_upward_atomically).
        ``report_kind``/``report_payload`` stay caller-supplied so the store owns no fact
        vocabulary (the same split as :meth:`enqueue_outbound`)."""
        ...

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        """Set the runtime workspace-prompt override (upsert) — read at spawn (issue #17)."""
        ...

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        """Stash a won claim's plaintext route token (upsert) — issue #84a.

        Called on a won claim with the token the claim response returned once. A fresh
        claim overwrites a prior row for the same chunk; a re-spawn under a route already
        held never calls this again, so :meth:`~IReadRunnerStore.route_token` keeps
        returning the same value across those paths."""
        ...

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        """Persist a lease's capability-token hash (issue #113, Phase 1).

        Every ``mint_lease_token`` caller records through here — spawn, resume re-mint,
        and takeover re-mint (issue #258) — so a lease id **is** re-minted and this write
        is overwrite-safe: the implementation replaces any prior row, invalidating the
        previous token. The plaintext is never persisted; only this sha256 hash lands
        here, read back via :meth:`~IReadRunnerStore.lease_token_hash`."""
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
        durable the instant this returns, and is read back via
        :meth:`pending_requeue_chunk_ids` — this call never spawns anything itself."""

    def record_usage(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        sample: UsageSample,
        recorded_at: datetime,
    ) -> None:
        """Idempotently record one usage fact **and** buffer its outbound report, atomically
        (issue #58) — mirrors :meth:`record_local_pause`'s atomic local-write + outbound-
        enqueue pairing: a fact the hub is never told about is never reconciled later.

        Keyed on ``(lease_id, generation, sample.kind)``: a resume within the same lease
        mints a new ``generation`` (:meth:`IReadRunnerStore.lease_generation`) and so is a
        genuinely new row (append-only); a replay of the exact same invocation finds the
        row already there and writes nothing a second time, buffering no duplicate report
        either."""
        ...

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> None:
        """Append one external-subscription-usage sampling attempt **and**, only when it
        produced a sample, buffer its outbound report — atomically (issue #218), mirroring
        :meth:`record_local_pause`'s atomic local-write + outbound-enqueue pairing.

        Always appends the attempt row (the cadence anchor
        :meth:`~IReadRunnerStore.last_external_usage_attempt_at` derives from), whether or
        not the harness had anything to report. Enqueues the outbound fact only when
        ``payload`` is not ``None``. ``report_kind``/``report_payload`` stay
        caller-supplied so the store owns no fact vocabulary (the same split as
        :meth:`record_local_pause` and :meth:`enqueue_outbound`)."""
        ...

    def record_attachment(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        name: str,
        content: str,
        attached_at: datetime,
    ) -> None:
        """Append a worker's explicit artifact submission for ``name`` (issue #113,
        Phase 2), a single committed transaction so it survives a ``kill -9`` between
        this call and the completion submission that would otherwise read it. Called
        from the domain layer, never directly from the API edge
        (``bzh:controller-read-only``). Append-only: a later call for the same
        ``(lease_id, name)`` is a correction, read back as the replacement by
        :meth:`~IReadRunnerStore.attachments_for_lease`, never merged with the prior
        row."""
        ...

    def record_git_commit_declaration(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        environment_id: str,
        repo: str,
        branch: str,
        commit: str,
        declared_at: datetime,
    ) -> None:
        """Append a worker's explicit git-commit declaration for ``repo`` in
        ``environment_id`` (issue #143, Phase 3), a single committed transaction so it
        survives a ``kill -9`` between this call and the collection that would otherwise
        read it. Called from the domain layer, never directly from the API edge
        (``bzh:controller-read-only``). Append-only: a later call for the same
        ``(lease_id, environment_id, repo)`` is a correction, read back as the
        replacement by :meth:`~IReadRunnerStore.git_commit_declarations_for_lease`, never
        merged with the prior row."""
        ...

    def record_nudge_fired(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably spend this attempt's one `produces`-unmet nudge (issue #113,
        Phase 4). Idempotent by its own check-then-insert, not a DB constraint
        (``bzh:sql-portable``), mirroring :meth:`record_usage`. Called *before* the
        resume that delivers the nudge — the ordering rationale lives at the call site
        in ``runner/loop/steps.py``."""
        ...

    def record_check_results(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        results: list[CheckResultRecord],
        at: datetime,
    ) -> None:
        """Append this attempt's check result rows (issue #114), one committed transaction
        so they survive a ``kill -9`` between the run and the marker that follows. Written
        BEFORE :meth:`record_checks_ran` so a marker never precedes its rows
        (``runner:checks-recorded-when-marked``). Re-run-safe: a recovery that finds
        :meth:`checks_ran` unset re-runs and re-records, latest-wins."""
        ...

    def record_checks_ran(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably mark this attempt's ``checks:`` as run (issue #114) — the guard
        :meth:`~IReadRunnerStore.checks_ran` reads. Written AFTER :meth:`record_check_results`
        and only for a node with a non-empty ``checks:``, so the marker implies its result
        rows exist. Idempotent by its own check-then-insert (``bzh:sql-portable``), mirroring
        :meth:`record_nudge_fired`."""
        ...

    def record_session_preamble(self, session_id: str, *, fingerprint: PreambleFingerprint, at: datetime) -> None:
        """Record what standing preamble prose this session was just sent (issue #149).

        Append-only; the newest row is what
        :meth:`~IReadRunnerStore.session_preamble_fingerprint` reads back. The fact is
        *"this prose was sent to this session"*, not *"a spawn happened"* — hence its own
        call rather than a widening of :meth:`record_spawn`, whose resume-with-message
        sites send no ``prompt_prefix`` and would poison the session's newest row.

        Called only from :func:`~blizzard.runner.loop.steps._spawn_attempt`, immediately
        after ``record_spawn``, so a durable fingerprint always implies the prose reached
        the process; a crash that loses it leaves the next resume rendering in full. See
        the recorded exemption in ``blizzard-context:/architecture/crash-correctness.md``."""
        ...
