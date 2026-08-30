"""The runner-store repository seam (``bzh:repository-split``/``bzh:dependency-inversion``).

Facts only, status derived (``bzh:facts-not-status``): an *active* lease is one with no
closure fact; a *held* env is one whose binding has no release fact. Every timestamp is
passed in from the injected clock — the store never reads a wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.runner.auth.tokens import IReadTokenRepository, IWriteTokenRepository
from blizzard.runner.domain.leases import IReadLeaseRepository, IWriteLeaseRepository, LeaseRecord
from blizzard.runner.environments.repository import (
    IReadEnvironmentRepository,
    IWriteEnvironmentRepository,
)
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository, IWriteWorkspacePromptRepository
from blizzard.runner.transcripts.ledger import (
    IReadTranscriptLedgerRepository,
    IWriteTranscriptLedgerRepository,
)


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
class GraphArtifactRecord:
    """One graph-scoped ``artifacts:`` declaration, pinned to the mint it was baked into
    — the runner's own mirror of the hub's ``graph_artifacts`` row, keyed
    ``(graph_id, name)``. ``ordinal`` is the authored ``artifacts:`` position, carried
    through as the envelope's own list order."""

    name: str
    ordinal: int
    kind: ArtifactKind
    content: str


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


class IReadRunnerStore(
    IReadLeaseRepository,
    IReadEnvironmentRepository,
    IReadTranscriptLedgerRepository,
    IReadTokenRepository,
    IReadWorkspacePromptRepository,
    Protocol,
):
    """Read-only runner-store queries (held by read-path edges)."""

    def pending_submission_lease_ids(self) -> set[str]:
        """Lease ids with an unacked ``completion.submitted`` or ``decision.submitted``
        fact in the buffer.

        ADVANCE's skip set, so a node-step's outcome is elicited exactly once while the
        flush is pending."""
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
        whether or not it has been forwarded up yet."""
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

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked (issue #13).

        The crash-time reference startup recovery classifies staleness against, stamped
        each tick, so the newest value is when the daemon died to within one tick."""
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

    def graph_artifacts_for_graph(self, graph_id: str) -> list[GraphArtifactRecord]:
        """This mint's pinned graph-scoped declarations, in authored order. Keyed on
        the mint's own ``graph_id``, never the lease — a lease pinned to a superseded mint
        keeps reading that mint's own rows. Empty for a mint that declared none, or one
        pinned before this runner ever recorded a pin."""
        ...


class IWriteRunnerStore(
    IWriteLeaseRepository,
    IWriteEnvironmentRepository,
    IWriteTranscriptLedgerRepository,
    IWriteTokenRepository,
    IWriteWorkspacePromptRepository,
    IReadRunnerStore,
    Protocol,
):
    """Read-write runner store — held only by the domain (the loop steps)."""

    def record_graph_artifacts(
        self, *, graph_id: str, artifacts: list[GraphArtifactRecord], recorded_at: datetime
    ) -> None:
        """Pin a mint's graph-scoped declarations, insert-if-absent: a second call
        for the same ``graph_id`` — a second lease against the same mint — writes nothing
        new. Called by ``Spawner._mint`` before :meth:`record_lease`, so a crash between
        the two leaves only an orphan row a retry re-writes identically."""
        ...

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        """Stamp the runner as alive at ``alive_at`` — the tick's liveness beat (issue #13).

        Upserted, one row per runner: only the newest instant matters, and it is the crash-time
        reference startup recovery reads back via :meth:`last_daemon_liveness`."""
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
    ) -> int:
        """Append a local pause/start fact **and** its hub-bound report, atomically
        (issue #43), and return the buffered report's seq. Appends rather than upserts:
        a locally-minted fact, not a mirror; taking the buffer entry here makes the
        brake and its report crash-atomic (``tests/test_ingest_and_pause_verbs.py``)."""
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
        """Close a takeover."""
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
        atomically (issue #58); return the buffered report's seq. Keyed on
        ``(lease_id, generation, sample.kind)``: a resume within the same lease is a
        genuinely new row; an exact replay writes nothing, buffers nothing, returns ``None``."""
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
        """Append one context-sample attempt and, when a report is given, buffer it and
        return its seq, atomically. ``context_tokens is None`` records an attempt that
        measured nothing, which still advances the cadence anchor. An empty
        ``report_kind`` records the sample alone — the ordinary case, since only a
        first crossing reports — and returns ``None``, no report buffered."""
        ...

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> int | None:
        """Append one external-subscription-usage sampling attempt **and**, only when it
        produced a sample, buffer its outbound report — atomically (issue #218). The
        attempt row is always appended, whether or not the harness had anything to
        report; the outbound fact exists only when ``payload`` is not ``None``, its seq
        returned then and ``None`` otherwise."""
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
