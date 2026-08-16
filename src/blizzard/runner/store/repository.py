"""The runner-store repository seam (``bzh:repository-split``/``bzh:dependency-inversion``).

Facts only, status derived (``bzh:facts-not-status``): an *active* lease is one with no
closure fact; a *held* env is one whose binding has no release fact. Every timestamp is
passed in from the injected clock — the store never reads a wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.usage import UsageSample


class RunnerStoreError(RuntimeError):
    """A runner-store operation failed — the domain-facing error the loop sees.

    Wraps the driver exception at the adapter boundary, so callers never depend on it."""


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
    # What session this attempt runs and under what configuration (issue #144), stamped on
    # the mint's own `lease_context` insert. `None` means *unknown*, never a value.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None


@dataclass(frozen=True)
class PoolHead:
    """A named session pool's current head (issue #144). ``resolved_model``/
    ``resolved_effort`` are the head's own **stamps**, not a fresh resolution; ``None``
    on either means *unknown*, never a value."""

    session_id: str
    lease_id: str
    resolved_model: str | None
    resolved_effort: str | None


@dataclass(frozen=True)
class LeaseRecord:
    """A lease joined with its node context — the loop's per-attempt fact.

    ``pid`` / ``process_start_time`` / ``session_id`` are ``None`` until spawn-return."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    runner_id: str
    retries_max: int
    created_at: datetime
    # This attempt's session stamps, read back (issue #144). `None` on any of the three
    # means *unknown*, never a value.
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None
    pid: int | None = None
    process_start_time: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ClosedLeaseRecord:
    """A lease joined with its closure fact — the panel's recent-history read (issue #29).

    ``reason`` is the closure vocabulary: ``transitioned`` | ``reaped`` | ``failed`` |
    ``escalated`` | ``parked`` | ``released``."""

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
    """One hub-bound fact off the outbound buffer, acked or not. The same table as
    :class:`BufferedFact`, read as a ledger: ``acked_at`` kept, ``payload`` dropped."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: datetime
    acked_at: datetime | None


@dataclass(frozen=True)
class TranscriptSegmentLedgerRow:
    """One row of the transcript segment ledger (issue #246, D2) — local state, never shipped
    as-is, and so named apart from the wire's own ``TranscriptSegmentRecord`` (blizzard#247).
    ``normalizer_version`` is never ``None``, starting at the source seam's "never ran"
    sentinel. ``truncated_reason``/``shipping_stopped_reason`` are independent: the former never latches."""

    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    lease_id: str
    session_id: str
    cursor: str | None
    shipped_bytes: int
    shipped_turns: int
    normalizer_version: str
    harness_version: str | None
    truncated_reason: str | None
    shipping_stopped_reason: str | None
    #: Set only on a re-ship (blizzard#250): the segment this one replaces on the hub.
    supersedes: str | None
    finalized_at: datetime | None
    stamped_at: datetime
    #: agent_id -> spawning `tool_use_id` (blizzard#338), accumulated across every window
    #: this segment has read; empty until one names a pair.
    agent_tool_use_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BufferedTranscriptDelta:
    """One pending record in the transcript lane's own buffer (D3) — :class:`BufferedFact`'s
    counterpart. Non-final ``payload`` is a ``TranscriptSegmentRecord``'s fields (minus
    ``seq``/``runner_id``) as JSON; a final one is just ``{"segment_id": ...}``. ``final``
    mirrors the payload's own flag, driving ack-time keep-vs-delete."""

    seq: int
    segment_id: str
    chunk_id: str
    final: bool
    payload: str
    created_at: datetime


@dataclass(frozen=True)
class TranscriptBackfillLease:
    """One session-bearing lease the backfill may import (blizzard#250), with whether that
    session already holds a segment. The dedupe key is the *session*: a pre-epic session
    resumed across leases left one merged file, which imports once."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    session_id: str
    has_segment: bool


@dataclass(frozen=True)
class AskRecord:
    """The worker's local open-ask fact.

    ``question_id`` is runner-minted so the answer polls back by it; ``session_id`` is
    the dormant session the resume-with-answer targets."""

    lease_id: str
    chunk_id: str
    question_id: str
    question: str
    options: list[str]
    session_id: str | None
    asked_at: datetime


@dataclass(frozen=True)
class ContextSampleState:
    """What a lease's recorded context samples establish so far — the sampler's own memory."""

    #: The newest sample's stamp: the cadence anchor, derived rather than a stored column.
    last_sampled_at: datetime
    #: The highest context measured, or ``None`` when no attempt measured one — the warn dedupe.
    max_context_tokens: int | None


@dataclass(frozen=True)
class UsageTotals:
    """A summed window of usage facts (issue #58). ``cost_partial`` carries the
    lower-bound contract on ``cost_usd``: a caller must check it before treating
    ``cost_usd`` as exact."""

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

    Open until a later lease is minted for the chunk, or the hub resolves it terminally and
    PULL records an ``escalation_closures`` mark (#292) — two supersessions, no flag."""

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

    Carries no forge: the origin it is verified against is read from the environment's
    repo manifest. ``environment_id`` is part of the identity, never a decoration."""

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

    ``lease_id`` always names the reference lease — active or already closed, never
    ``None``. ``fence_epoch`` is set only when a live worker was force-killed."""

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

    def lease(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id, regardless of closure — the transcript read (issue #29).

        Distinct from :meth:`active_lease`: a transcript outlives its lease.
        """
        ...

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        """The most recently closed leases, newest first — the panel's recent-history
        read (issue #29).

        ``limit`` bounds rows returned, never how long a closure fact lives on disk.
        """
        ...

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        """The lease's most recent heartbeat stamp, or ``None`` if it never beat.

        REAP's stall signal; on ``None`` the caller falls back to :meth:`latest_spawn`."""
        ...

    def latest_spawn(self, lease_id: str) -> datetime | None:
        """When this lease's newest process was spawned, or ``None`` if it never was.

        The second half of REAP's staleness baseline (issue #150). A lease outlives its
        processes, so the newest ``lease_spawns`` row is when the running worker started."""
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

    def transcript_segment(self, segment_id: str) -> TranscriptSegmentLedgerRow | None:
        """The segment by id, or ``None`` — the pump and drain's per-segment read (issue #246)."""
        ...

    def open_transcript_segments(self) -> list[TranscriptSegmentLedgerRow]:
        """Segments with no final marker yet — the pump's per-tick work list (issue #246)."""
        ...

    def chunk_transcript_shipped_bytes(self, chunk_id: str) -> int:
        """Sum of ``shipped_bytes`` across every one of this chunk's segments, open or
        finalized — the running total the 64 MB per-chunk budget (D4) is measured against."""
        ...

    def outstanding_transcript_buffer_bytes(self) -> int:
        """Sum of ``payload`` bytes across every UNACKED row of the transcript outbound
        buffer, across every segment (F8, review round 7) — the pump's own backpressure
        gate against a prolonged hub outage leaving unbounded content resident in SQLite.
        Distinct from :meth:`chunk_transcript_shipped_bytes`, which bounds one chunk's
        SHIPPED total, not the buffer's own resident total."""
        ...

    def has_unshipped_transcript_content(self, chunk_id: str) -> bool:
        """Whether this chunk holds an UNACKED **content** row in the transcript outbound
        buffer (issue #249) — the "not yet acked by the hub" half of the panel's home
        selection. Final markers are excluded deliberately: a pending one carries no turns,
        so the hub's copy is already complete. An existence check, not
        :meth:`pending_transcript_outbound`'s payload-materializing list read."""
        ...

    def pending_transcript_outbound(self, *, limit: int | None = None) -> list[BufferedTranscriptDelta]:
        """The unacked transcript buffer, FIFO by seq — the drain's own lane (D3).

        ``limit`` bounds the query itself, not just what the caller iterates — a large
        backlog's full payload set (up to the per-record cap each) is otherwise materialized
        before any per-run bound the caller applies is ever consulted."""
        ...

    def transcript_backfill_leases(self) -> list[TranscriptBackfillLease]:
        """Every lease that ever recorded a session id, oldest first — the backfill's work
        list (blizzard#250). This store is the only source: the harness directory holds the
        operator's own sessions too, and a sweep of it could never tell them apart."""
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
        whether or not it has been forwarded up yet."""
        ...

    def held_bindings(self) -> list[EnvBindingRecord]:
        """Every currently-held env binding, across every chunk (issue #51).

        :meth:`bindings_for_chunk` widened from one chunk to the whole fleet this runner
        holds, on the same ``held`` predicate."""
        ...

    def open_escalations(self) -> list[EscalationRecord]:
        """Every escalated chunk still unsuperseded (issue #51).

        See :class:`EscalationRecord` for what "open" means here."""
        ...

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        """The chunk's open escalation, or ``None`` (issue #53).

        The single-chunk narrowing of :meth:`open_escalations`. Unaffected by a takeover
        in between — a takeover writes neither a closure nor a lease mint."""
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

        Distinct from ``hub_paused``: it blocks every spawn site, not claims alone (issue
        #45). Defaults False when the operator has never set it."""
        ...

    def resume_intent_lease_ids(self) -> set[str]:
        """Leases carrying an **open** restart resume-intent.

        A ``resume_intents`` mark with no ``resume_clears`` for the same lease at or
        after it (#12, #13). Empty on any normal tick; non-empty only on the first tick
        after a restart."""
        ...

    def session_ended_lease_ids(self) -> set[str]:
        """Leases whose **current spawn** recorded a session-end — it declared done.

        A dead pid *with* a session-end is a done declaration, not a crash to re-attach.
        Scoped to the lease's newest ``lease_spawns`` fact, because a lease outlives its
        sessions and an unscoped read would suppress every later crash's resume."""
        ...

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked (issue #13).

        The crash-time reference startup recovery classifies staleness against, stamped
        each tick, so the newest value is when the daemon died to within one tick."""
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

    def lease_for_open_takeover(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id iff an open takeover names it (issue #291), regardless of the
        lease's own closure — the worker-authorization resolver's second half, alongside
        :meth:`active_lease`. The open-takeover fact is what authorizes a resumed session's
        worker verbs against the reference lease it names, not the lease's own activeness."""
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

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        """What this lease's context samples already establish, or ``None`` if none exist.

        One read answering both of the sampler's questions — when it last sampled (the
        cadence anchor) and the highest context it has seen (whether the warn line has
        already been crossed, so the warning fires once rather than every sample)."""
        ...

    def last_external_usage_attempt_at(self) -> datetime | None:
        """The derived cadence anchor for the external-subscription-usage sample step
        (issue #218): ``max(sampled_at)`` across ``external_usage_samples``, or ``None``.

        Derived, never a stored column (``bzh:facts-not-status``). A NULL-``payload``
        attempt counts exactly like a successful one — this runner *tried* then."""
        ...

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        """The lease's explicit artifact submissions, newest content per ``name``
        (issue #113). Append-only, latest-wins-per-``(lease_id, name)``: a re-attach of
        the same name reads back as the replacement, never a duplicate."""
        ...

    def git_commit_declarations_for_lease(self, lease_id: str) -> dict[tuple[str, str], GitCommitDeclarationRecord]:
        """The lease's explicit git-commit declarations, newest per ``(environment_id,
        repo)`` (issue #143, Phase 3), keyed the same way.

        Append-only, latest-wins. Keying on the environment as well as the repo keeps
        several environments from collapsing one env's branch onto another's."""
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
        durable (issue #114). Written *after* the result rows, so ``True`` implies the
        rows exist (``runner:checks-recorded-when-marked``); a crash between them leaves
        this ``False``, which safely re-runs."""
        ...

    def check_results_for_lease(self, lease_id: str, epoch: int) -> list[CheckResultRecord]:
        """This attempt's recorded check results, in run order (issue #114). Empty for an
        attempt whose checks never ran (or a node with no ``checks:``)."""
        ...

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        """The standing preamble prose this session was last sent, or ``None`` (issue #149).

        The newest ``session_preamble_facts`` row for the session. ``None`` renders the
        full preamble — the safe direction, since an over-eager match would cost the
        worker its updated instructions."""
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
    ) -> int | None:
        """Close a lease — a clean transition or a failure/escalation.

        When ``event_kind``/``event_payload`` are given (issue #125), the event is
        enqueued to the outbound buffer **in the same transaction** as the closure, so
        the two land together or not at all."""
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

    def mark_transcript_record_truncated(self, segment_id: str, *, reason: str, severity: int) -> bool:
        """Note that one shipped record was shrunk in place (D4's per-record cap) —
        informational only. Latches per ``(segment_id, reason)`` (F2): the SAME reason
        recurring never re-warns; a DIFFERENT one always does, regardless of what currently
        displays. ``severity`` ranks ``reason`` against this method's other callers — the
        store keeps whichever arrived with the highest severity as the displayed one."""
        ...

    def stop_transcript_segment_shipping(self, segment_id: str, *, reason: str) -> bool:
        """Permanently stop shipping this segment's content — the per-chunk 64 MB budget
        breached (D4). The only field :class:`TranscriptPump`'s guard reads; idempotent,
        keeps its first reason. Returns whether this call actually set the field."""
        ...

    def mark_sidechain_dropped_warned(self, segment_id: str, *, agent_id: str | None) -> bool:
        """Latch the dropped-sidechain fact-lane warning per (segment, agent_id): a subagent
        conversation can outlive one pump window, so this must not re-warn every tick it
        stays unlinked. Returns whether this is the first warning for this agent."""
        ...

    def record_transcript_deltas(
        self,
        *,
        segment_id: str,
        chunk_id: str,
        cursor: str | None,
        shipped_bytes: int,
        shipped_turns: int,
        normalizer_version: str,
        harness_version: str | None,
        payloads: list[str],
        created_at: datetime,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> list[int]:
        """Advance a segment's cursor/shipped counts/version stamp and atomically enqueue
        ``len(payloads)`` buffer rows (issue #246; F1) — ONE transaction, so a batch split
        into several records still advances the cursor exactly once, and a crash loses
        neither the cursor advance nor any record. Returns their seqs, in payload order."""
        ...

    def open_transcript_segment(
        self,
        *,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        lease_id: str,
        session_id: str,
        stamped_at: datetime,
        supersedes: str | None = None,
    ) -> str:
        """Stamp a segment boundary outside a spawn and return its id (blizzard#250), cursor
        unset so the pump reads the session from the start. Every boundary the *live* lane
        stamps stays :meth:`record_spawn`'s; this one is the backfill's alone. ``supersedes``
        is the re-ship's own pointer at the segment this one replaces on the hub."""
        ...

    def finalize_transcript_segment(self, segment_id: str, *, finalized_at: datetime) -> bool:
        """Close one segment out on its own, enqueuing its single final marker in the same
        transaction — :meth:`record_closure`'s per-segment half, for a segment whose lease
        closed long before it existed. ``False`` when it was already finalized."""
        ...

    def advance_transcript_cursor(
        self,
        segment_id: str,
        *,
        cursor: str,
        normalizer_version: str,
        harness_version: str | None,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> None:
        """Advance a segment's read cursor (and version stamp) with nothing to enqueue — a
        window that moved the source's read position but produced no turn (e.g. a run of
        control records), which still must not be re-read next tick. Unlike
        :meth:`record_transcript_deltas`, no outbound row: there is no record to ship, only
        progress to remember."""
        ...

    def ack_transcript_outbound(self, seq: int, *, acked_at: datetime) -> None:
        """Ack a buffered transcript row — the drain's own ack (D3). A ``delta`` row is
        pruned outright (up to the per-record cap each, nothing reads one acked); a ``final`` row
        stays, marked acked — its own tiny row is the exactly-once receipt
        :class:`~blizzard.foundation.store.invariants.TranscriptSegmentFinalizedExactlyOnce`
        checks for."""
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
    ) -> int:
        """Append a local pause/start fact **and** its hub-bound report, atomically (issue #43).

        Appends rather than upserts: this is a locally-minted fact, not a mirror. Taking
        the buffer entry here is what makes the brake and its report crash-atomic (pinned
        by ``tests/test_ingest_and_pause_verbs.py``)."""
        ...

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        """Set the runtime workspace-prompt override (upsert) — read at spawn (issue #17)."""
        ...

    def clear_workspace_prompt(self, workspace_id: str) -> bool:
        """Drop the runtime workspace-prompt override, returning whether one was there (#344).

        Removing the row is what distinguishes clearing from overriding with empty text: the
        absent row is the only state that resolves back to the configured prompt."""
        ...

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        """Stash a won claim's plaintext route token (upsert) — issue #84a.

        Called on a won claim with the token the claim response returned once. A fresh
        claim overwrites a prior row for the same chunk."""
        ...

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        """Persist a lease's capability-token hash (issue #113, Phase 1).

        Overwrite-safe: the implementation replaces any prior row, invalidating the
        previous token. The plaintext is never persisted, only this sha256 hash."""
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

    def record_escalation_closure(self, *, chunk_id: str, reason: str, at: datetime) -> None:
        """Mirror the hub having ended a chunk this runner holds an escalation for (#292, #293).

        The supersession no lease mint can supply: a terminal chunk is never claimed again.
        ``reason`` is the hub status observed — ``stopped`` or ``done``."""

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
    ) -> int | None:
        """Idempotently record one usage fact **and** buffer its outbound report,
        atomically (issue #58).

        Keyed on ``(lease_id, generation, sample.kind)``: a resume within the same lease
        is a genuinely new row; an exact replay writes nothing and buffers nothing."""
        ...

    def record_context_sample(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        session_id: str,
        context_tokens: int | None,
        sampled_at: datetime,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        """Append one context-sample attempt, and buffer its outbound report when one is given,
        atomically. ``context_tokens is None`` records an attempt that measured nothing, which
        still advances the cadence anchor. An empty ``report_kind`` records the sample alone —
        the ordinary case, since only a first crossing reports — and returns ``None`` then,
        since no report was buffered."""
        ...

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> int | None:
        """Append one external-subscription-usage sampling attempt **and**, only when it
        produced a sample, buffer its outbound report — atomically (issue #218).

        The attempt row is always appended, whether or not the harness had anything to
        report; the outbound fact exists only when ``payload`` is not ``None``."""
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
        """Append a worker's explicit artifact submission for ``name`` (issue #113), a
        single committed transaction so it survives a ``kill -9`` before the completion
        submission reads it. Append-only: a later call for the same ``(lease_id, name)``
        is a correction, read back as the replacement, never merged."""
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
        ``environment_id`` (issue #143), a single committed transaction so it survives a
        ``kill -9`` before the collection reads it. Append-only: a later call for the
        same key is a correction, read back as the replacement, never merged."""
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

        Append-only; the newest row is what the fingerprint read returns. The fact is
        *"this prose was sent to this session"*, not *"a spawn happened"*, and is written
        after the spawn so a durable fingerprint implies the prose reached the process."""
        ...
