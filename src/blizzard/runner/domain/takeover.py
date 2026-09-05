"""The operator takeover — ``blizzard runner takeover <chunk-id>`` (issue #52).

A chunk is **takeable** while this runner holds it and carries no running attempt; anything else
raises a refusal the edge maps to ``409``. The **fact-before-command** ordering holds regardless
of ``force`` (``bzh:crash-correctness``): the takeover fact, which makes the chunk unreachable to
every loop step, lands first — and a forced kill writes no attempt fact, so it consumes no retry."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import TAKEOVER_PREFIX, Id
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerPreamble
from blizzard.runner.loop.process import IProcessProbe
from blizzard.wire.facts import LEASE_MINTED

if TYPE_CHECKING:
    # Deferred: ``runner/stores.py`` composes this module's own Protocol (blizzard#410).
    from blizzard.runner.environments.repository import EnvBindingRecord
    from blizzard.runner.stores import RunnerStores

# What a takeover forwards from the identity env (issue #258). Nothing else leaves the
# daemon: the operator's terminal supplies the rest, and no secret crosses the local API.
_IDENTITY_PREFIX = "BLIZZARD_"
_FORWARDED_EXECUTION_VARS = ("PATH", "HOME")

__all__ = [
    "ChunkNotTakeable",
    "IReadTakeoverRepository",
    "IWriteTakeoverRepository",
    "LiveWorkerConflict",
    "OpenedTakeover",
    "SubmissionPending",
    "TakeoverCloseScope",
    "TakeoverCommand",
    "TakeoverOpenScope",
    "TakeoverRecord",
    "TakeoverService",
]


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


@dataclass(frozen=True)
class TakeoverOpenScope:
    """The chunk-keyed facts :meth:`TakeoverService.open` reads, resolved at the edge
    (``bzh:domain-takes-objects``): the runner holds no chunk entity, so this names
    exactly the facts the rule's refusals and reference-lease derivation read from
    ``chunk_id`` — the open takeover, the held bindings, the active and latest leases,
    and the fence-epoch floor."""

    chunk_id: str
    open_takeover: TakeoverRecord | None
    bindings: list[EnvBindingRecord]
    active_lease: LeaseRecord | None
    latest_lease: LeaseRecord | None
    latest_epoch: int


@dataclass(frozen=True)
class TakeoverCloseScope:
    """The chunk-keyed fact :meth:`TakeoverService.close` reads, resolved at the edge
    (``bzh:domain-takes-objects``)."""

    chunk_id: str
    open_takeover: TakeoverRecord | None


class IReadTakeoverRepository(Protocol):
    """Read-only takeover queries (held by read-path edges)."""

    def lease_for_open_takeover(self, lease_id: str) -> LeaseRecord | None:
        """The lease by id iff an open takeover names it (issue #291), regardless of the
        lease's own closure — the worker-authorization resolver's second half, alongside
        :meth:`~blizzard.runner.domain.leases.IReadLeaseRecordRepository.active_lease`. The
        open-takeover fact is what authorizes a resumed session's worker verbs against the
        reference lease it names, not the lease's own activeness."""
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
        :mod:`~blizzard.runner.domain.escalations`'s own ``open_escalations`` shape — the
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


class TakeoverError(Exception):
    """Base for the takeover domain's refusals — the API edge maps these to HTTP."""


class ChunkNotTakeable(TakeoverError):
    """The chunk holds no binding, already carries an open takeover, or has no
    resumable session to hand the operator."""


class LiveWorkerConflict(TakeoverError):
    """A live worker attempt is running and ``force`` was not given."""


class SubmissionPending(TakeoverError):
    """The lease's completion (or gate decision) is already buffered, unacked.

    A fence minted now would sit at a higher buffer seq than the queued submission, which is
    strict FIFO, so the submission would flush first and the fence never take effect."""


class TakeoverEndedElsewhere(TakeoverError):
    """No open takeover matches the given id — already closed, or never opened."""


@dataclass(frozen=True)
class OpenedTakeover:
    """What :meth:`TakeoverService.open` returns — the CLI execs ``command`` verbatim."""

    takeover_id: str
    command: str
    workdir: str
    # The declared pool this session belongs to (issue #144); ``None`` when it belongs to
    # no pool, or predates the stamps.
    session_name: str | None = None
    # The bounded takeover env (issue #258), layered over the operator's terminal on exec.
    # Carries the re-minted lease token — env only, never the printable ``command``.
    env: dict[str, str] = field(default_factory=dict)


class TakeoverService:
    """Composition-root-wired: the clock, harness, and process probe (issue #52).

    Spans five concepts (takeover, asks, outbound, tokens, elicitations), so it holds the
    :class:`~blizzard.runner.stores.RunnerStores` bundle (D4) — the chunk-keyed reads
    (environments, leases) are resolved at the edge instead (``bzh:domain-takes-objects``)."""

    def __init__(
        self,
        stores: RunnerStores,
        clock: IClock,
        harness: IHarnessAdapter,
        process: IProcessProbe,
        *,
        local_api_url: str,
        events: IRunnerEventPublisher | None = None,
    ) -> None:
        self._stores = stores
        self._clock = clock
        self._harness = harness
        self._process = process
        self._local_api_url = local_api_url
        # The SSE publish seam (D2), typed against the Protocol (``bzh:dependency-inversion``);
        # ``None`` on a broker-less app, a no-op there.
        self._events = events

    def open(self, scope: TakeoverOpenScope, *, force: bool) -> OpenedTakeover:
        """Open a takeover over ``scope.chunk_id``, or raise a ``409``-mapped refusal.
        ``scope`` is already resolved by the caller (``bzh:domain-takes-objects``)."""
        chunk_id = scope.chunk_id
        if scope.open_takeover is not None:
            raise ChunkNotTakeable(f"chunk {chunk_id} already has an open takeover")
        if not scope.bindings:
            raise ChunkNotTakeable(f"chunk {chunk_id} is not held by this runner — nothing to take over")
        workdir = scope.bindings[0].workdir

        active = scope.active_lease
        live = active is not None and active.lease_id not in self._stores.asks.parked_lease_ids()
        if live and not force:
            raise LiveWorkerConflict(f"chunk {chunk_id} has a live worker attempt — pass --force to take it over")
        if (
            live
            and force
            and active is not None
            and active.lease_id in self._stores.outbound.pending_submission_lease_ids()
        ):
            raise SubmissionPending(f"chunk {chunk_id}'s attempt already submitted — let it land, then `requeue`")

        reference: LeaseRecord | None = active if active is not None else scope.latest_lease
        if reference is None or reference.session_id is None:
            raise ChunkNotTakeable(f"chunk {chunk_id} has no resumable session to take over")
        session_id = reference.session_id

        now = self._clock.now()
        takeover_id = Id.mint(TAKEOVER_PREFIX, self._clock).value
        fence_epoch = scope.latest_epoch + 1 if live else None

        # Fact-before-command (bzh:crash-correctness): recorded — and so reachable by
        # every loop step's open-takeover skip — before anything is killed or returned.
        self._stores.takeover.record_takeover(
            takeover_id=takeover_id,
            chunk_id=chunk_id,
            lease_id=reference.lease_id,
            session_id=session_id,
            workdir=workdir,
            fence_epoch=fence_epoch,
            opened_at=now,
        )
        if self._events is not None:
            self._events.publish_takeover_changed(chunk_id, takeover_id, cause="opened")

        if live and active is not None:
            # The fence bump: reported like a fresh lease mint, so the killed worker's
            # buffered completion lands on a stale epoch.
            seq = self._stores.outbound.enqueue_outbound(
                kind=LEASE_MINTED,
                chunk_id=chunk_id,
                lease_id=None,
                payload=json.dumps({"chunk_id": chunk_id, "epoch": fence_epoch}),
                created_at=now,
            )
            if self._events is not None:
                self._events.publish_fact_changed(seq=seq, kind=LEASE_MINTED, chunk_id=chunk_id, lease_id=None)
            if active.pid is not None:
                self._process.kill(active.pid)  # the reap machinery's own best-effort kill
            # A taken-over chunk's lease is skipped by every loop step from here on (Advance,
            # Reap alike), so an in-flight elicitation would otherwise leak forever uncollected
            # and unkilled (blizzard#443, D7) — killed here, the one path that closes it out.
            elicitation = self._stores.elicitations.in_flight_elicitation(active.lease_id, active.epoch)
            if elicitation is not None:
                if elicitation.pid is not None:
                    self._process.kill(elicitation.pid)
                self._stores.elicitations.clear_elicitation(active.lease_id, active.epoch)

        # Read the reference lease's stamps (issue #144) rather than re-resolving, so the
        # operator continues under exactly the configuration the session ran with.
        command = self._harness.resume_command(
            workdir,
            session_id,
            model=reference.resolved_model,
            effort=reference.resolved_effort,
            attended=True,
        )
        # A resume inherits no spawn env, so identity must be handed over (issue #258).
        # The token plaintext is never persisted, so it is re-minted, invalidating the prior.
        lease_token, token_hash = LeaseToken.mint()
        self._stores.tokens.record_lease_token(reference.lease_id, token_hash, now)
        preamble = WorkerPreamble(
            environments=[
                AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in scope.bindings
            ],
            lease_id=reference.lease_id,
            local_api_url=self._local_api_url,
            lease_token=lease_token,
        )
        # Bound what leaves the daemon: never the whole allowlisted child env, which
        # carries terminal vars that would clobber the operator's and any secret.
        full_env = self._harness.identity_env(preamble, chunk_id, session_id)
        env = {
            name: value
            for name, value in full_env.items()
            if name.startswith(_IDENTITY_PREFIX) or name in _FORWARDED_EXECUTION_VARS
        }
        return OpenedTakeover(
            takeover_id=takeover_id,
            command=command,
            workdir=workdir,
            session_name=reference.session_name,
            env=env,
        )

    def close(self, scope: TakeoverCloseScope, takeover_id: str) -> None:
        """End ``takeover_id``, idempotently (issue #291): ending one already ended — by this
        same call racing ``Pull``'s own closer, or a retried end-PATCH — is the desired state,
        so it succeeds rather than raising. Only a genuinely *different* takeover holding the
        chunk is the real conflict this still refuses. ``scope`` is already resolved by the
        caller (``bzh:domain-takes-objects``)."""
        record = scope.open_takeover
        if record is not None and record.takeover_id != takeover_id:
            raise TakeoverEndedElsewhere(f"takeover {takeover_id} on chunk {scope.chunk_id} is not open")
        if record is None:
            return
        self._stores.takeover.record_takeover_end(takeover_id=takeover_id, ended_at=self._clock.now())
        if self._events is not None:
            self._events.publish_takeover_changed(scope.chunk_id, takeover_id, cause="closed")
