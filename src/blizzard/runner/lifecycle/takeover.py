"""The operator takeover of a chunk this runner holds.

A chunk is **takeable** while this runner holds it and carries no running attempt; anything else
raises a ``TakeoverError`` refusal. The **fact-before-command** ordering holds regardless of
``force`` (``bzh:crash-point-registry``): the open-takeover fact lands first — and a forced kill
writes no attempt fact, so it consumes no retry."""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.fact_kinds import LEASE_MINTED
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.runner.auth.tokens import IWriteTokenRepository
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.environments.workspaces import IWorkspaceSpawnRoots
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessWorkerLifecycle, WorkerPreamble
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessLifecycleRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.hub.outbound_buffer import IWriteOutboundRepository
from blizzard.runner.leases.elicitation import IWriteElicitationRepository
from blizzard.runner.leases.escalations import resume_workdir
from blizzard.runner.leases.lease_auth import LeaseToken
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.process.owned_process import IOwnedProcessControl, kill_owned_process

if TYPE_CHECKING:
    from blizzard.runner.environments.repository import EnvBinding

# What a takeover forwards from the identity env. Nothing else leaves the
# daemon: the operator's terminal supplies the rest, and no secret crosses the local API.
_IDENTITY_PREFIX = "BLIZZARD_"
_FORWARDED_EXECUTION_VARS = ("PATH", "HOME")

# The armed crash windows (``bzh:crash-point-registry``) of a forced entry over a live worker. The decision is
# durable at the first, the displacement done at the second; startup recovery finishes and ends either.
_CP_TAKEOVER_AFTER_OPEN = crashpoint(
    "takeover.after-open.before-kill",
    "runner committed the takeover decision but has killed nothing — restart must finish the kill and end the entry",
)
_CP_TAKEOVER_AFTER_KILL = crashpoint(
    "takeover.after-kill.before-entry",
    "runner killed the worker but has not recorded the entry — restart must end the entry",
)

__all__ = [
    "TAKEOVER_TRANSITIONS",
    "ChunkNotTakeable",
    "EscalationCommands",
    "IReadTakeoverRepository",
    "IWriteTakeoverRepository",
    "LiveWorkerConflict",
    "OpenTakeover",
    "OpenedTakeover",
    "SubmissionPending",
    "TakeoverAdmission",
    "TakeoverCloseScope",
    "TakeoverCommand",
    "TakeoverDisplacement",
    "TakeoverError",
    "TakeoverOpenScope",
    "TakeoverOwnerUnresolvable",
    "TakeoverRecovery",
    "TakeoverService",
    "TakeoverState",
    "TakeoverVerb",
    "admit_takeover",
    "bounded_takeover_env",
    "resolve_resume_command",
    "takeover_closing",
]


class TakeoverState(StrEnum):
    """Where a chunk stands with respect to takeovers."""

    #: No takeover is open over the chunk.
    NONE = "none"
    #: A person holds the chunk's session.
    OPEN = "open"


class TakeoverVerb(StrEnum):
    OPEN = "open"
    #: End the open takeover the caller names.
    END_OWN = "end-own"
    #: End a takeover id that is not the open one.
    END_OTHER = "end-other"


class TakeoverVerdict(StrEnum):
    APPLY = "apply"
    NO_OP = "no-op"
    REFUSE = "refuse"


#: Which takeover verbs are legal from which state; ending when none is open is a no-op.
TAKEOVER_TRANSITIONS: Mapping[TakeoverState, Mapping[TakeoverVerb, TakeoverVerdict]] = MappingProxyType(
    {
        TakeoverState.NONE: MappingProxyType(
            {
                TakeoverVerb.OPEN: TakeoverVerdict.APPLY,
                TakeoverVerb.END_OWN: TakeoverVerdict.NO_OP,
                TakeoverVerb.END_OTHER: TakeoverVerdict.NO_OP,
            }
        ),
        TakeoverState.OPEN: MappingProxyType(
            {
                TakeoverVerb.OPEN: TakeoverVerdict.REFUSE,
                TakeoverVerb.END_OWN: TakeoverVerdict.APPLY,
                TakeoverVerb.END_OTHER: TakeoverVerdict.REFUSE,
            }
        ),
    }
)


@domain_model
@dataclass(frozen=True)
class OpenTakeover:
    """An open operator takeover — the human-in-session fact. ``lease_id`` names the reference lease,
    active or closed; ``fence_epoch`` is set only when a live worker was force-killed; ``reference_epoch``
    is the reference lease's own epoch; ``hold_epoch`` is the chunk's latest epoch when the takeover
    opened, above the reference lease when a sessionless lease (an escalation mint) followed it."""

    takeover_id: str
    chunk_id: str
    lease_id: str | None
    session_id: str | None
    workdir: str
    fence_epoch: int | None
    opened_at: datetime
    harness_id: str | None = None
    reference_epoch: int | None = None
    hold_epoch: int | None = None
    #: Whether the entry completed — its commands ran and the person was handed the session.
    entered: bool = True

    def interrupted(self) -> bool:
        """Open with no entry fact: a runner crash cut the entry short, and no person holds the session."""
        return not self.entered

    def holds(self, chunk_id: str, epoch: int | None) -> bool:
        """Whether this takeover keeps the loop off a lease (or a held chunk) at ``epoch``: the
        person holds the reference lease, the chunk's own epoch at open, and anything at or below
        the fence. A lease a later re-claim mints sits above all three and is the loop's again; a
        takeover recording no epoch holds the whole chunk."""
        if chunk_id != self.chunk_id:
            return False
        epochs = (self.reference_epoch, self.fence_epoch, self.hold_epoch)
        ceiling = max((e for e in epochs if e is not None), default=None)
        return ceiling is None or epoch is None or epoch <= ceiling

    def ended_by(self, view: ChunkState) -> bool:
        """The hub has ended the chunk, so the takeover's authorization must not outlive it."""
        return view.status in TERMINAL_STATUSES

    @property
    def session(self) -> SessionReference | None:  # ast-grep-ignore: bzh:property-delegates
        if self.session_id is None:
            return None
        if self.harness_id is None:
            raise ValueError(f"takeover {self.takeover_id} has session_id {self.session_id!r} but no harness_id")
        return SessionReference(self.harness_id, self.session_id)


@domain_model
@dataclass(frozen=True)
class TakeoverOpenScope:
    """The chunk-keyed facts :meth:`TakeoverService.open` reads, resolved at the edge
    (``bzh:domain-takes-objects``): the runner holds no chunk entity, so this names
    exactly the facts the rule's refusals and reference-lease derivation read from
    ``chunk_id`` — the open takeover, the held bindings, the active and latest leases,
    the fence-epoch floor, whether a runner requeue of the chunk is pending, whether the active
    lease is ask-parked or already has a submission buffered, and the workspace the chunk's newest binding names."""

    chunk_id: str
    open_takeover: OpenTakeover | None
    bindings: list[EnvBinding]
    active_lease: Lease | None
    latest_lease_with_session: Lease | None
    latest_epoch: int
    requeue_pending: bool = False
    active_parked: bool = False
    submission_pending: bool = False
    #: The workspace the chunk's newest binding names — the session resumes in its spawn cwd; ``None`` names none.
    workspace_id: str | None = None


@domain_model
@dataclass(frozen=True)
class TakeoverCloseScope:
    """The chunk-keyed fact :meth:`TakeoverService.close` reads, resolved at the edge
    (``bzh:domain-takes-objects``)."""

    chunk_id: str
    open_takeover: OpenTakeover | None


class IReadTakeoverRepository(Protocol):
    """Read-only takeover queries."""

    def lease_for_open_takeover(self, lease_id: str) -> Lease | None:
        """The lease by id iff an open takeover names it, regardless of the
        lease's own closure — the worker-authorization resolver's second half, alongside
        :meth:`~blizzard.runner.leases.record.IReadLeaseRecordRepository.active_lease`. The
        open-takeover fact is what authorizes a resumed session's worker verbs against the
        reference lease it names, not the lease's own activeness."""
        ...

    def open_takeover_for_chunk(self, chunk_id: str) -> OpenTakeover | None:
        """The chunk's open takeover, or ``None`` — a ``takeovers`` row with no
        ``takeover_ends`` row for the same ``takeover_id``.

        At most one open takeover exists per chunk at a time."""
        ...

    def open_takeovers_for(self, chunk_ids: Iterable[str]) -> dict[str, OpenTakeover]:
        """:meth:`open_takeover_for_chunk` for every chunk in ``chunk_ids`` in one batched read,
        keyed by chunk id; a chunk with no open takeover is absent (`bzh:bulk-reconstitution`)."""
        ...

    def open_takeover_chunk_ids(self) -> set[str]:
        """Every chunk id currently under an open takeover: each names a chunk
        whose session the human holds, untouchable until the takeover closes."""
        ...

    def open_takeovers(self) -> list[OpenTakeover]:
        """Every open takeover, across every chunk.

        :meth:`open_takeover_for_chunk` widened to the fleet, mirroring
        :mod:`~blizzard.runner.leases.escalations`'s own ``open_escalations`` shape — the
        read that names a takeover left open by a stranded CLI, otherwise wedging its chunk."""
        ...


class IWriteTakeoverRepository(IReadTakeoverRepository, Protocol):
    """Read-write takeover store — held only by the domain."""

    def record_takeover(
        self,
        *,
        takeover_id: str,
        chunk_id: str,
        lease_id: str | None,
        workdir: str,
        fence_epoch: int | None,
        opened_at: datetime,
        session: SessionReference,
        hold_epoch: int | None = None,
        fence_payload: str | None = None,
        lease_token_hash: str | None = None,
        entered_at: datetime | None = None,
    ) -> int | None:
        """Open a takeover — recorded before any kill and before the interactive command
        is returned, so no later tick can race the human for the chunk.

        The takeover row, the ``lease.minted`` fence fact (``fence_payload``), the reference lease's re-minted
        token hash and — when nothing is left to kill — the entry fact (``entered_at``) commit in ONE transaction.
        Returns the fence fact's ``seq``, ``None`` when no fence was written."""
        ...

    def record_takeover_entry(self, *, takeover_id: str, entered_at: datetime) -> None:
        """Record that the takeover's entry completed. Idempotent."""
        ...

    def record_takeover_end(self, *, takeover_id: str, ended_at: datetime) -> None:
        """Close a takeover."""
        ...


@domain_model
@dataclass(frozen=True)
class TakeoverCommand:
    """The ``blizzard runner takeover`` CLI invocation an escalation composes when it can —
    composed here so the form lives beside the concept it names rather than inline at each
    call site. Every operand is shell-quoted: a hub-minted chunk id never needs it
    (``src/blizzard/foundation/ids.py`` grammar), but the composed string is pasted into a shell.

    ``runner`` names the runner holding the chunk: a host serving several refuses an omitted
    ``--runner``, so the pasted command must carry it. Empty omits the flag."""

    chunk_id: str
    runner_dir: str
    runner: str = ""

    @property
    def wrapped(self) -> str:
        return self._compose()

    def _compose(self) -> str:
        command = f"blizzard runner takeover {shlex.quote(self.chunk_id)} --dir {shlex.quote(self.runner_dir)}"
        return f"{command} --runner {shlex.quote(self.runner)}" if self.runner else command

    @classmethod
    def wrapped_for(cls, chunk_id: str, *, resume_command: str, runner_dir: str, runner: str = "") -> str | None:
        """The wrapped command an escalation carries beside ``resume_command``, or ``None``
        when it can carry none: no raw resume command to wrap, or no known runner dir."""
        if not resume_command or not runner_dir:
            return None
        return cls(chunk_id, runner_dir, runner).wrapped


@domain_model
@dataclass(frozen=True)
class EscalationCommands:
    """The raw resume command and its wrapped takeover form an escalation carries — the one
    composition both the escalating write and the status read use. Wrapped-vs-raw rules:
    `blizzard-context:/domain/humans/escalation.md` §The commands an escalation carries.

    ``resume`` is ``""`` when no command can be composed; ``wrapped`` is ``None`` when no
    raw command exists to wrap or the runner dir is unknown."""

    resume: str
    wrapped: str | None

    @classmethod
    def compose(cls, chunk_id: str, *, resume: str, runner_dir: str, runner: str = "") -> EscalationCommands:
        """Composed around ``resume``, the raw command the caller already resolved from the
        escalation's own stamps (see :func:`resolve_resume_command`); ``""`` composes no command."""
        return cls(
            resume=resume,
            wrapped=TakeoverCommand.wrapped_for(chunk_id, resume_command=resume, runner_dir=runner_dir, runner=runner),
        )


def resolve_resume_command(
    *,
    session: SessionReference | None,
    bindings: Sequence[EnvBinding],
    harness: IHarnessWorkerLifecycle | None,
    model: str | None,
    effort: str | None,
    workspace_root: str,
) -> str:
    """The raw resume command for an escalation, built from its own stamps (``model``,
    ``effort``) so a takeover lands in exactly the configuration the parked session ran
    with, never a fresh resolution. ``harness`` is the session owner already resolved,
    ``None`` when it cannot be; a missing session, held binding, or harness yields ``""``."""
    workdir = resume_workdir(session, bindings)
    if session is None or workdir is None or harness is None:
        return ""
    return harness.resume_command(
        SpawnCwd.of_session(workspace_root, workdir), session.session_id, model=model, effort=effort
    )


class TakeoverError(Exception):
    """Base for the takeover domain's refusals — the API edge maps these to HTTP."""


class ChunkNotTakeable(TakeoverError):
    """The chunk holds no binding, already carries an open takeover, or has no
    resumable session to hand the operator."""


class LiveWorkerConflict(TakeoverError):
    """A live worker attempt is running and ``force`` was not given."""


class TakeoverOwnerUnresolvable(TakeoverError):
    """The reference session's recorded owner cannot be dispatched to right now, so the
    takeover would offer no usable command."""


class SubmissionPending(TakeoverError):
    """The lease's completion (or gate decision) is already buffered, unacked.

    A fence minted now would sit at a higher buffer seq than the queued submission, which is
    strict FIFO, so the submission would flush first and the fence never take effect."""


class TakeoverEndedElsewhere(TakeoverError):
    """No open takeover matches the given id — already closed, or never opened."""


@domain_model
@dataclass(frozen=True)
class TakeoverAdmission:
    """An admitted takeover: the session it hands over, and whether it supersedes a live worker."""

    reference: Lease
    session: SessionReference
    workdir: str
    #: A worker is live — the takeover force-kills it and fences its epoch.
    live: bool
    #: The epoch the fence mints, above every epoch this runner knows; ``None`` when nothing is live.
    fence_epoch: int | None
    #: The chunk's latest epoch at open — held even when the reference lease sits below it.
    hold_epoch: int


def admit_takeover(scope: TakeoverOpenScope, *, force: bool) -> TakeoverAdmission:
    """Admit a takeover over ``scope.chunk_id``, or raise the refusal.

    The chunk must be held here with no open takeover and no pending runner requeue. A live worker — an
    active lease that is not parked, since each such can still land a verdict — refuses unless forced,
    and a forced entry over an already-buffered submission is refused, as the fence could never apply."""
    chunk_id = scope.chunk_id
    if TAKEOVER_TRANSITIONS[_state(scope.open_takeover)][TakeoverVerb.OPEN] is TakeoverVerdict.REFUSE:
        raise ChunkNotTakeable(
            f"chunk {chunk_id} already has an open takeover — end it with `blizzard runner takeover {chunk_id} --end`"
        )
    if not scope.bindings:
        raise ChunkNotTakeable(f"chunk {chunk_id} is not held by this runner — nothing to take over")
    if scope.requeue_pending:
        raise ChunkNotTakeable(f"chunk {chunk_id} has a runner requeue pending — let it spawn, then take over")
    active = scope.active_lease
    live = active is not None and not scope.active_parked
    if live and not force:
        raise LiveWorkerConflict(f"chunk {chunk_id} has a live worker attempt — pass --force to take it over")
    if live and scope.submission_pending:
        raise SubmissionPending(f"chunk {chunk_id}'s attempt already submitted — let it land, then `requeue`")
    reference = active if active is not None else scope.latest_lease_with_session
    if reference is None or reference.session is None:
        raise ChunkNotTakeable(f"chunk {chunk_id} has no resumable session to take over")
    return TakeoverAdmission(
        reference=reference,
        session=reference.session,
        workdir=scope.bindings[0].workdir,
        live=live,
        fence_epoch=scope.latest_epoch + 1 if live else None,
        hold_epoch=scope.latest_epoch,
    )


def bounded_takeover_env(full_env: Mapping[str, str]) -> dict[str, str]:
    """What leaves the daemon: the identity variables and the execution basics, never the whole
    allowlisted child env, which carries terminal variables that would clobber the operator's
    and any secret."""
    return {
        name: value
        for name, value in full_env.items()
        if name.startswith(_IDENTITY_PREFIX) or name in _FORWARDED_EXECUTION_VARS
    }


def takeover_closing(scope: TakeoverCloseScope, takeover_id: str) -> OpenTakeover | None:
    """The open takeover ending ``takeover_id`` closes, or ``None`` when there is nothing to close;
    ``TAKEOVER_TRANSITIONS`` decides."""
    record = scope.open_takeover
    verb = TakeoverVerb.END_OWN if record is None or record.takeover_id == takeover_id else TakeoverVerb.END_OTHER
    verdict = TAKEOVER_TRANSITIONS[_state(record)][verb]
    if verdict is TakeoverVerdict.REFUSE:
        raise TakeoverEndedElsewhere(f"takeover {takeover_id} on chunk {scope.chunk_id} is not open")
    return record if verdict is TakeoverVerdict.APPLY else None


def _state(open_takeover: OpenTakeover | None) -> TakeoverState:
    return TakeoverState.NONE if open_takeover is None else TakeoverState.OPEN


@domain_model
@dataclass(frozen=True)
class OpenedTakeover:
    """What :meth:`TakeoverService.open` returns — the CLI execs ``command`` verbatim."""

    takeover_id: str
    command: str
    workdir: str
    # The declared pool this session belongs to; ``None`` when it belongs to
    # no pool, or predates the stamps.
    session_name: str | None = None
    harness_id: str | None = None
    # The bounded takeover env, layered over the operator's terminal on exec.
    # Carries the re-minted lease token — env only, never the printable ``command``.
    env: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class TakeoverCloser:
    """Ends a takeover through :func:`takeover_closing` — the one close path."""

    takeover: IWriteTakeoverRepository
    clock: IClock
    events: IRunnerEventPublisher | None = None

    def close(self, scope: TakeoverCloseScope, takeover_id: str) -> None:
        """End ``takeover_id``, idempotently: ending one already ended is the desired state, so
        it succeeds rather than raising. Only a different takeover holding the chunk refuses."""
        if takeover_closing(scope, takeover_id) is None:
            return
        self.takeover.record_takeover_end(takeover_id=takeover_id, ended_at=self.clock.now())
        if self.events is not None:
            self.events.publish_takeover_changed(scope.chunk_id, takeover_id, cause="closed")


@dataclass(frozen=True)
class TakeoverDisplacement:
    """The commands that displace a live worker: the best-effort kill, then the kill and clear of any in-flight
    elicitation. Every step is liveness- and identity-checked, so a repeat is safe."""

    process: IOwnedProcessControl
    elicitations: IWriteElicitationRepository

    def run(self, lease: Lease) -> None:
        # The reap machinery's own best-effort kill: the shared, liveness-checked,
        # pgid-preferring kill every owned-process teardown reaches through.
        kill_owned_process(self.process, pid=lease.pid, process_start_time=lease.process_start_time, pgid=lease.pgid)
        # A taken-over chunk's lease is never driven again, so an in-flight elicitation
        # is killed here or never.
        elicitation = self.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        if elicitation is not None:
            kill_owned_process(
                self.process,
                pid=elicitation.pid,
                process_start_time=elicitation.process_start_time,
                pgid=elicitation.pgid,
            )
            self.elicitations.clear_elicitation(lease.lease_id, lease.epoch)


@dataclass(frozen=True)
class TakeoverRecovery:
    """Finishes then ends every takeover whose entry a runner crash interrupted, once at startup.

    Run before the daemon serves a request, when no entry can be mid-flight. The token plaintext died with the
    crashed request, so no person holds the session: the displacement is re-driven (the fence stands at the
    hub, the lease stays open), then the takeover ends — the state of "entered, then left" — and the
    operator's retry is admitted as a fresh entry. A crash during recovery re-runs at the next startup: the
    kill is idempotent and the end is a lone insert."""

    takeover: IWriteTakeoverRepository
    leases: IReadLeaseRecordRepository
    elicitations: IWriteElicitationRepository
    process: IOwnedProcessControl
    clock: IClock
    events: IRunnerEventPublisher | None = None

    def run(self) -> int:
        """End each interrupted takeover; answer how many."""
        interrupted = [t for t in self.takeover.open_takeovers() if t.interrupted()]
        for record in interrupted:
            lease = self.leases.lease(record.lease_id) if record.lease_id is not None else None
            if lease is not None:
                TakeoverDisplacement(self.process, self.elicitations).run(lease)
            self.takeover.record_takeover_end(takeover_id=record.takeover_id, ended_at=self.clock.now())
            if self.events is not None:
                self.events.publish_takeover_changed(record.chunk_id, record.takeover_id, cause="closed")
        return len(interrupted)


class TakeoverService:
    """Composition-root-wired: the clock, harness registry, and process probe.

    The chunk-keyed reads (environments, leases) are resolved at the edge
    (``bzh:domain-takes-objects``)."""

    def __init__(
        self,
        clock: IClock,
        process: IOwnedProcessControl,
        *,
        takeover: IWriteTakeoverRepository,
        outbound: IWriteOutboundRepository,
        tokens: IWriteTokenRepository,
        elicitations: IWriteElicitationRepository,
        local_api_url: str,
        harnesses: IHarnessLifecycleRegistry,
        workspaces: IWorkspaceSpawnRoots,
        events: IRunnerEventPublisher | None = None,
    ) -> None:
        self._takeover = takeover
        self._outbound = outbound
        self._tokens = tokens
        self._elicitations = elicitations
        self._workspaces = workspaces
        self._clock = clock
        self._harnesses = harnesses
        self._process = process
        self._local_api_url = local_api_url
        # The SSE publish seam, typed against the Protocol (``bzh:dependency-inversion``);
        # ``None`` on a broker-less app, a no-op there.
        self._events = events

    def open(self, scope: TakeoverOpenScope, *, force: bool) -> OpenedTakeover:
        """Open a takeover over ``scope.chunk_id``, or raise a ``409``-mapped refusal.
        ``scope`` is already resolved by the caller (``bzh:domain-takes-objects``)."""
        chunk_id = scope.chunk_id
        active = scope.active_lease
        admission = admit_takeover(scope, force=force)
        reference, session, workdir, live = admission.reference, admission.session, admission.workdir, admission.live
        # Resolve before the fact-before-command write: an unavailable recorded owner blocks
        # this takeover rather than opening it and then offering no usable command.
        try:
            harness = self._harnesses.lifecycle(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            raise TakeoverOwnerUnresolvable(str(exc)) from exc
        now = self._clock.now()
        takeover_id = Id.mint(IdPrefix.TAKEOVER, self._clock).value
        fence_epoch = admission.fence_epoch
        # A resume inherits no spawn env, so identity must be handed over. The token plaintext is never
        # persisted, so it is re-minted, invalidating the prior — in the decision transaction, so the displaced
        # worker loses runner-verb authority in the same instant the takeover holds its lease.
        lease_token, token_hash = LeaseToken.mint()
        kills = live and active is not None

        # Fact-before-command (bzh:crash-point-registry): the whole decision — the takeover, the fence fact for
        # a live entry, the re-minted token, and the entry itself when nothing is left to kill — commits
        # in one transaction before anything is killed or returned.
        seq = self._takeover.record_takeover(
            takeover_id=takeover_id,
            chunk_id=chunk_id,
            lease_id=reference.lease_id,
            session=session,
            workdir=workdir,
            fence_epoch=fence_epoch,
            hold_epoch=admission.hold_epoch,
            opened_at=now,
            # The fence bump: reported like a fresh lease mint, so the killed worker's
            # buffered completion lands on a stale epoch.
            fence_payload=json.dumps({"chunk_id": chunk_id, "epoch": fence_epoch}) if kills else None,
            lease_token_hash=token_hash,
            entered_at=None if kills else now,
        )
        if self._events is not None:
            self._events.publish_takeover_changed(chunk_id, takeover_id, cause="opened")
            if seq is not None:
                self._events.publish_fact_changed(seq=seq, kind=LEASE_MINTED, chunk_id=chunk_id, lease_id=None)

        if kills and active is not None:
            _CP_TAKEOVER_AFTER_OPEN.reached()
            TakeoverDisplacement(self._process, self._elicitations).run(active)
            _CP_TAKEOVER_AFTER_KILL.reached()
            # The entry fact: the commands ran, the person is handed the session. Without it a restart
            # finishes the displacement and ends the entry.
            self._takeover.record_takeover_entry(takeover_id=takeover_id, entered_at=self._clock.now())

        # Read the reference lease's stamps rather than re-resolving, so the
        # operator continues under exactly the configuration the session ran with.
        command = harness.resume_command(
            SpawnCwd.of_session(self._workspaces.spawn_root(scope.workspace_id), workdir),
            session.session_id,
            model=reference.resolved_model,
            effort=reference.resolved_effort,
            attended=True,
        )
        preamble = WorkerPreamble(
            environments=[
                AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in scope.bindings
            ],
            lease_id=reference.lease_id,
            local_api_url=self._local_api_url,
            lease_token=lease_token,
        )
        env = bounded_takeover_env(harness.identity_env(preamble, chunk_id, session.session_id))
        return OpenedTakeover(
            takeover_id=takeover_id,
            command=command,
            workdir=workdir,
            session_name=reference.session_name,
            harness_id=session.harness_id,
            env=env,
        )

    def close(self, scope: TakeoverCloseScope, takeover_id: str) -> None:
        """End ``takeover_id``, idempotently: ending one already ended — by this
        same call racing ``Pull``'s own closer, or a retried end-PATCH — is the desired state,
        so it succeeds rather than raising. Only a genuinely *different* takeover holding the
        chunk is the real conflict this still refuses. ``scope`` is already resolved by the
        caller (``bzh:domain-takes-objects``)."""
        TakeoverCloser(self._takeover, self._clock, self._events).close(scope, takeover_id)
