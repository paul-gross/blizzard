"""The operator takeover — ``blizzard runner takeover <chunk-id>`` (issue #52).

Behind ``POST /chunks/{id}/takeovers`` / ``PATCH /chunks/{id}/takeovers/{tid}``:
resolves environment, session, and harness itself, so the operator addresses only the
chunk they have in hand from ``runner status`` or the board.

A chunk is **takeable** while this runner holds it (a live env binding) and carries
**no running attempt** — the three parked shapes issue #52 names (needs_human, parked
on an ask, parked at a gate) all satisfy this: none has a *live* worker, whether
because the lease closed (escalated / gate-parked) or is merely dormant (ask-parked).
:meth:`TakeoverService.open` raises :class:`ChunkNotTakeable` for anything else
(no binding, already under an open takeover, or no resumable session) and
:class:`LiveWorkerConflict` for a genuine live attempt with no ``force`` — both map to
``409`` at the API edge.

The **fact-before-command** ordering (``bzh:crash-correctness``) holds regardless of
``force``: :meth:`open` records the takeover fact — which is what makes the chunk
unreachable to every loop step (``ctx.store.open_takeover_chunk_ids()``, read by REAP
and ADVANCE) — before it kills anything or composes the interactive command, so no
supervisor tick can race the human for the chunk. ``force`` additionally kills the live
worker's pid (the reap machinery's own best-effort ``SIGKILL``) *after* the fact lands,
and reports a bumped epoch to the hub exactly like a reaped lease's requeue would — but
records no execution-attempt fact (no ``lease_context`` row, no closure), so the kill
consumes no retry and triggers no escalation: the attempt is superseded, not failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import TAKEOVER_PREFIX, mint
from blizzard.runner.domain.lease_auth import mint_lease_token
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerPreamble
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord
from blizzard.wire.facts import LEASE_MINTED

# What a takeover forwards from the adapter's identity env (issue #258): the lease's
# ``BLIZZARD_*`` identity, plus the two execution vars an interactive resume needs from
# the daemon side — ``PATH`` (the deployment venv, so bare ``blizzard`` resolves) and
# ``HOME`` (the harness session store ``--resume`` reads). Nothing else leaves the
# daemon: the operator's own terminal supplies the rest, and a ``[worker]
# env_passthrough`` secret never crosses the local API in a response body.
_IDENTITY_PREFIX = "BLIZZARD_"
_FORWARDED_EXECUTION_VARS = ("PATH", "HOME")

__all__ = [
    "ChunkNotTakeable",
    "LiveWorkerConflict",
    "OpenedTakeover",
    "SubmissionPending",
    "TakeoverService",
]


class TakeoverError(Exception):
    """Base for the takeover domain's refusals — the API edge maps these to HTTP."""


class ChunkNotTakeable(TakeoverError):
    """The chunk holds no binding, already carries an open takeover, or has no
    resumable session to hand the operator."""


class LiveWorkerConflict(TakeoverError):
    """A live worker attempt is running and ``force`` was not given."""


class SubmissionPending(TakeoverError):
    """The lease's completion (or gate decision) is already buffered, unacked.

    A fence minted now would land at a *higher* buffer seq than the already-queued
    submission — PULL is strict FIFO, so the submission would still flush and advance
    the node before the fence took effect. Not force-fencible; wait for the flush to
    land, then ``requeue``."""


class TakeoverEndedElsewhere(TakeoverError):
    """No open takeover matches the given id — already closed, or never opened."""


@dataclass(frozen=True)
class OpenedTakeover:
    """What :meth:`TakeoverService.open` returns — the CLI execs ``command`` verbatim."""

    takeover_id: str
    command: str
    workdir: str
    # The declared pool this session belongs to (issue #144), so an operator can be told
    # *which* lineage they are taking over rather than only its opaque session id.
    # ``None`` for a session on the bare/``resume:<node>`` vocabulary, which belongs to
    # no pool, and for one predating the stamps.
    session_name: str | None = None
    # The bounded takeover env (issue #258) — the lease's ``BLIZZARD_*`` identity plus
    # ``PATH``/``HOME`` — for the CLI to layer over the operator's terminal env on exec.
    # Carries the freshly re-minted lease token — env only, never part of the printable
    # ``command`` string.
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
            # A fence minted now buffers *behind* the already-queued completion (or
            # gate decision) — FIFO PULL flushes the submission first, advancing the
            # node before the fence could take effect. Not force-fencible.
            raise SubmissionPending(f"chunk {chunk_id}'s attempt already submitted — let it land, then `requeue`")

        reference: LeaseRecord | None = active if active is not None else self._store.latest_lease_for_chunk(chunk_id)
        if reference is None or reference.session_id is None:
            raise ChunkNotTakeable(f"chunk {chunk_id} has no resumable session to take over")
        session_id = reference.session_id

        now = self._clock.now()
        takeover_id = mint(TAKEOVER_PREFIX, self._clock)
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
            # The fence bump: reported to the hub exactly like a fresh lease mint, so the
            # killed worker's buffered completion lands on a stale epoch — but no
            # ``lease_context``/closure is written, so no retry is consumed and no
            # escalation follows.
            self._store.enqueue_outbound(
                kind=LEASE_MINTED,
                chunk_id=chunk_id,
                lease_id=None,
                payload=json.dumps({"chunk_id": chunk_id, "epoch": fence_epoch}),
                created_at=now,
            )
            if active.pid is not None:
                self._process.kill(active.pid)  # the reap machinery's own best-effort kill

        # Read the reference lease's stamps (issue #144) rather than re-resolving: the
        # operator continues under exactly the configuration the session ran with, not
        # whatever a fresh resolution would produce now. `None` on any of them is
        # *unknown* (a lease predating the stamps) and renders the bare command.
        command = self._harness.resume_command(
            workdir,
            session_id,
            model=reference.resolved_model,
            effort=reference.resolved_effort,
            attended=True,
        )
        # The taken-over session's worker identity (issue #258): ``--resume`` inherits no
        # spawn env, so without these vars its ``blizzard runner`` verbs (attach/ask/
        # artifact) cannot reach the runner. Identity is all it gets — the command above
        # carries no ``--settings``, so no heartbeat/SessionEnd hook is installed. The
        # token plaintext is never persisted, so it is **re-minted** here — invalidating
        # the prior one — exactly as the loop's ``_resume_preamble`` does for
        # daemon-driven resumes. The env rides the API response and the CLI's exec,
        # never the printable ``command`` above.
        lease_token, token_hash = mint_lease_token()
        self._store.record_lease_token(reference.lease_id, token_hash, now)
        preamble = WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings],
            lease_id=reference.lease_id,
            local_api_url=self._local_api_url,
            lease_token=lease_token,
        )
        # Bound what leaves the daemon: forward only the identity vars plus the named
        # execution vars, never the adapter's whole allowlisted child env — the base
        # allowlist carries the daemon's TERM/LANG/LC_* (which would clobber the
        # operator's terminal on exec) and any ``env_passthrough`` secret.
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
