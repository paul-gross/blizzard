"""The operator takeover — ``blizzard runner takeover <chunk-id>`` (issue #52).

A chunk is **takeable** while this runner holds it and carries no running attempt; anything else
raises a refusal the edge maps to ``409``. The **fact-before-command** ordering holds regardless
of ``force`` (``bzh:crash-correctness``): the takeover fact, which makes the chunk unreachable to
every loop step, lands first — and a forced kill writes no attempt fact, so it consumes no retry."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import TAKEOVER_PREFIX, Id
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerPreamble
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord
from blizzard.wire.facts import LEASE_MINTED

# What a takeover forwards from the identity env (issue #258). Nothing else leaves the
# daemon: the operator's terminal supplies the rest, and no secret crosses the local API.
_IDENTITY_PREFIX = "BLIZZARD_"
_FORWARDED_EXECUTION_VARS = ("PATH", "HOME")

__all__ = [
    "ChunkNotTakeable",
    "LiveWorkerConflict",
    "OpenedTakeover",
    "SubmissionPending",
    "TakeoverCommand",
    "TakeoverService",
]


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
    """Composition-root-wired: the store, clock, harness, and process probe (issue #52)."""

    def __init__(
        self,
        store: IWriteRunnerStore,
        clock: IClock,
        harness: IHarnessAdapter,
        process: IProcessProbe,
        *,
        local_api_url: str,
    ) -> None:
        self._store = store
        self._clock = clock
        self._harness = harness
        self._process = process
        self._local_api_url = local_api_url

    def open(self, chunk_id: str, *, force: bool) -> OpenedTakeover:
        if self._store.open_takeover_for_chunk(chunk_id) is not None:
            raise ChunkNotTakeable(f"chunk {chunk_id} already has an open takeover")
        bindings = self._store.bindings_for_chunk(chunk_id)
        if not bindings:
            raise ChunkNotTakeable(f"chunk {chunk_id} is not held by this runner — nothing to take over")
        workdir = bindings[0].workdir

        active = self._store.active_lease_for_chunk(chunk_id)
        live = active is not None and active.lease_id not in self._store.parked_lease_ids()
        if live and not force:
            raise LiveWorkerConflict(f"chunk {chunk_id} has a live worker attempt — pass --force to take it over")
        if live and force and active is not None and active.lease_id in self._store.pending_submission_lease_ids():
            raise SubmissionPending(f"chunk {chunk_id}'s attempt already submitted — let it land, then `requeue`")

        reference: LeaseRecord | None = active if active is not None else self._store.latest_lease_for_chunk(chunk_id)
        if reference is None or reference.session_id is None:
            raise ChunkNotTakeable(f"chunk {chunk_id} has no resumable session to take over")
        session_id = reference.session_id

        now = self._clock.now()
        takeover_id = Id.mint(TAKEOVER_PREFIX, self._clock).value
        fence_epoch = self._store.latest_epoch(chunk_id) + 1 if live else None

        # Fact-before-command (bzh:crash-correctness): recorded — and so reachable by
        # every loop step's open-takeover skip — before anything is killed or returned.
        self._store.record_takeover(
            takeover_id=takeover_id,
            chunk_id=chunk_id,
            lease_id=reference.lease_id,
            session_id=session_id,
            workdir=workdir,
            fence_epoch=fence_epoch,
            opened_at=now,
        )

        if live and active is not None:
            # The fence bump: reported like a fresh lease mint, so the killed worker's
            # buffered completion lands on a stale epoch.
            self._store.enqueue_outbound(
                kind=LEASE_MINTED,
                chunk_id=chunk_id,
                lease_id=None,
                payload=json.dumps({"chunk_id": chunk_id, "epoch": fence_epoch}),
                created_at=now,
            )
            if active.pid is not None:
                self._process.kill(active.pid)  # the reap machinery's own best-effort kill

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
        self._store.record_lease_token(reference.lease_id, token_hash, now)
        preamble = WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings],
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

    def close(self, chunk_id: str, takeover_id: str) -> None:
        record = self._store.open_takeover_for_chunk(chunk_id)
        if record is None or record.takeover_id != takeover_id:
            raise TakeoverEndedElsewhere(f"takeover {takeover_id} on chunk {chunk_id} is not open")
        self._store.record_takeover_end(takeover_id=takeover_id, ended_at=self._clock.now())
