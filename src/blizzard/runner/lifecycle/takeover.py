"""The operator takeover — ``blizzard runner takeover <chunk-id>``.

A chunk is **takeable** while this runner holds it and carries no running attempt; anything else
raises a refusal the edge maps to ``409``. The **fact-before-command** ordering holds regardless
of ``force`` (``bzh:crash-correctness``): the takeover fact, which makes the chunk unreachable to
every loop step, lands first — and a forced kill writes no attempt fact, so it consumes no retry."""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.clock import IClock
from blizzard.foundation.fact_kinds import LEASE_MINTED
from blizzard.foundation.ids import TAKEOVER_PREFIX, Id
from blizzard.foundation.roles import domain_model
from blizzard.runner.auth.tokens import IWriteTokenRepository
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessWorkerLifecycle, WorkerPreamble
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessLifecycleRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.hub.outbound_buffer import IWriteOutboundRepository
from blizzard.runner.leases import Lease
from blizzard.runner.leases.elicitation import IWriteElicitationRepository
from blizzard.runner.leases.escalations import resume_workdir
from blizzard.runner.leases.lease_auth import LeaseToken
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.process.owned_process import IOwnedProcessControl, kill_owned_process

if TYPE_CHECKING:
    from blizzard.runner.environments.repository import EnvBinding

# What a takeover forwards from the identity env. Nothing else leaves the
# daemon: the operator's terminal supplies the rest, and no secret crosses the local API.
_IDENTITY_PREFIX = "BLIZZARD_"
_FORWARDED_EXECUTION_VARS = ("PATH", "HOME")

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
    "TakeoverError",
    "TakeoverOpenScope",
    "TakeoverOwnerUnresolvable",
    "TakeoverService",
    "TakeoverState",
    "TakeoverVerb",
    "admit_takeover",
    "bounded_takeover_env",
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
    the fence-epoch floor, whether a runner requeue of the chunk is pending, and whether the active
    lease is ask-parked or already has a submission buffered."""

    chunk_id: str
    open_takeover: OpenTakeover | None
    bindings: list[EnvBinding]
    active_lease: Lease | None
    latest_lease_with_session: Lease | None
    latest_epoch: int
    requeue_pending: bool = False
    active_parked: bool = False
    submission_pending: bool = False


@domain_model
@dataclass(frozen=True)
class TakeoverCloseScope:
    """The chunk-keyed fact :meth:`TakeoverService.close` reads, resolved at the edge
    (``bzh:domain-takes-objects``)."""

    chunk_id: str
    open_takeover: OpenTakeover | None


class IReadTakeoverRepository(Protocol):
    """Read-only takeover queries (held by read-path edges)."""

    def lease_for_open_takeover(self, lease_id: str) -> Lease | None:
        """The lease by id iff an open takeover names it, regardless of the
        lease's own closure — the worker-authorization resolver's second half, alongside
        :meth:`~blizzard.runner.leases.IReadLeaseRecordRepository.active_lease`. The
        open-takeover fact is what authorizes a resumed session's worker verbs against the
        reference lease it names, not the lease's own activeness."""
        ...

    def open_takeover_for_chunk(self, chunk_id: str) -> OpenTakeover | None:
        """The chunk's open takeover, or ``None`` — a ``takeovers`` row with no
        ``takeover_ends`` row for the same ``takeover_id``.

        At most one open takeover exists per chunk at a time."""
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
    ) -> None:
        """Open a takeover — recorded before any kill and before the interactive command
        is returned, so no later tick can race the human for the chunk."""
        ...

    def record_takeover_end(self, *, takeover_id: str, ended_at: datetime) -> None:
        """Close a takeover."""
        ...


@domain_model
@dataclass(frozen=True)
class TakeoverCommand:
    """The ``blizzard runner takeover`` CLI invocation an escalation composes when it can —
    composed here so the form lives beside the concept it names rather than inline at each
    call site. Both operands are shell-quoted: a hub-minted chunk id never needs it
    (``foundation/ids.py`` grammar), but the composed string is pasted into a shell."""

    chunk_id: str
    runner_dir: str

    @property
    def wrapped(self) -> str:
        return f"blizzard runner takeover {shlex.quote(self.chunk_id)} --dir {shlex.quote(self.runner_dir)}"

    @classmethod
    def wrapped_for(cls, chunk_id: str, *, resume_command: str, runner_dir: str) -> str | None:
        """The wrapped command an escalation carries beside ``resume_command``, or ``None``
        when it can carry none: no raw resume command to wrap, or no known runner dir."""
        if not resume_command or not runner_dir:
            return None
        return cls(chunk_id, runner_dir).wrapped


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
    def compose(
        cls,
        chunk_id: str,
        *,
        session: SessionReference | None,
        bindings: Sequence[EnvBinding],
        harness: IHarnessWorkerLifecycle | None,
        model: str | None,
        effort: str | None,
        workspace_root: str,
        runner_dir: str,
    ) -> EscalationCommands:
        """Composed from the escalation's own stamps (``model``, ``effort``), so a takeover
        lands in exactly the configuration the parked session ran with, never a fresh
        resolution. ``harness`` is the session owner already resolved, ``None`` when it cannot
        be; a missing session, held binding, or harness composes no command."""
        workdir = resume_workdir(session, bindings)
        if session is None or workdir is None or harness is None:
            return cls(resume="", wrapped=None)
        resume = harness.resume_command(
            SpawnCwd.of_session(workspace_root, workdir), session.session_id, model=model, effort=effort
        )
        return cls(
            resume=resume, wrapped=TakeoverCommand.wrapped_for(chunk_id, resume_command=resume, runner_dir=runner_dir)
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
        raise ChunkNotTakeable(f"chunk {chunk_id} already has an open takeover")
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
    """The open takeover ending ``takeover_id`` closes, or ``None`` when none is open — ending one
    already ended is the desired state. Another takeover holding the chunk refuses."""
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
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TakeoverCloser:
    """Ends a takeover through :func:`takeover_closing` — the one close path, shared by the
    operator's end request and the loop's hub-ended sweep."""

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
        workspace_root: str,
        events: IRunnerEventPublisher | None = None,
    ) -> None:
        self._takeover = takeover
        self._outbound = outbound
        self._tokens = tokens
        self._elicitations = elicitations
        self._workspace_root = workspace_root
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
        takeover_id = Id.mint(TAKEOVER_PREFIX, self._clock).value
        fence_epoch = admission.fence_epoch

        # Fact-before-command (bzh:crash-correctness): recorded — and so reachable by
        # every loop step's open-takeover skip — before anything is killed or returned.
        self._takeover.record_takeover(
            takeover_id=takeover_id,
            chunk_id=chunk_id,
            lease_id=reference.lease_id,
            session=session,
            workdir=workdir,
            fence_epoch=fence_epoch,
            hold_epoch=admission.hold_epoch,
            opened_at=now,
        )
        if self._events is not None:
            self._events.publish_takeover_changed(chunk_id, takeover_id, cause="opened")

        if live and active is not None:
            # The fence bump: reported like a fresh lease mint, so the killed worker's
            # buffered completion lands on a stale epoch.
            seq = self._outbound.enqueue_outbound(
                kind=LEASE_MINTED,
                chunk_id=chunk_id,
                lease_id=None,
                payload=json.dumps({"chunk_id": chunk_id, "epoch": fence_epoch}),
                created_at=now,
            )
            if self._events is not None:
                self._events.publish_fact_changed(seq=seq, kind=LEASE_MINTED, chunk_id=chunk_id, lease_id=None)
            # The reap machinery's own best-effort kill: the shared, liveness-checked,
            # pgid-preferring kill every owned-process teardown reaches through.
            kill_owned_process(
                self._process, pid=active.pid, process_start_time=active.process_start_time, pgid=active.pgid
            )
            # A taken-over chunk's lease is skipped by every loop step from here on (Advance,
            # Reap alike), so an in-flight elicitation would otherwise leak forever uncollected
            # and unkilled — killed here, the one path that closes it out.
            elicitation = self._elicitations.in_flight_elicitation(active.lease_id, active.epoch)
            if elicitation is not None:
                kill_owned_process(
                    self._process,
                    pid=elicitation.pid,
                    process_start_time=elicitation.process_start_time,
                    pgid=elicitation.pgid,
                )
                self._elicitations.clear_elicitation(active.lease_id, active.epoch)

        # Read the reference lease's stamps rather than re-resolving, so the
        # operator continues under exactly the configuration the session ran with.
        command = harness.resume_command(
            SpawnCwd.of_session(self._workspace_root, workdir),
            session.session_id,
            model=reference.resolved_model,
            effort=reference.resolved_effort,
            attended=True,
        )
        # A resume inherits no spawn env, so identity must be handed over.
        # The token plaintext is never persisted, so it is re-minted, invalidating the prior.
        lease_token, token_hash = LeaseToken.mint()
        self._tokens.record_lease_token(reference.lease_id, token_hash, now)
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
