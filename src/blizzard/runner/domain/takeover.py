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
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import TAKEOVER_PREFIX, mint
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord
from blizzard.wire.facts import LEASE_MINTED

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


class TakeoverService:
    """Composition-root-wired: the store, clock, harness, and process probe (issue #52)."""

    def __init__(
        self, store: IWriteRunnerStore, clock: IClock, harness: IHarnessAdapter, process: IProcessProbe
    ) -> None:
        self._store = store
        self._clock = clock
        self._harness = harness
        self._process = process

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
        session_id = reference.session_id if reference is not None else None
        if session_id is None:
            raise ChunkNotTakeable(f"chunk {chunk_id} has no resumable session to take over")

        now = self._clock.now()
        takeover_id = mint(TAKEOVER_PREFIX, self._clock)
        fence_epoch = self._store.latest_epoch(chunk_id) + 1 if live else None

        # Fact-before-command (bzh:crash-correctness): recorded — and so reachable by
        # every loop step's open-takeover skip — before anything is killed or returned.
        self._store.record_takeover(
            takeover_id=takeover_id,
            chunk_id=chunk_id,
            lease_id=reference.lease_id if reference is not None else None,
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

        command = self._harness.resume_command(workdir, session_id)
        return OpenedTakeover(takeover_id=takeover_id, command=command, workdir=workdir)

    def close(self, chunk_id: str, takeover_id: str) -> None:
        record = self._store.open_takeover_for_chunk(chunk_id)
        if record is None or record.takeover_id != takeover_id:
            raise TakeoverEndedElsewhere(f"takeover {takeover_id} on chunk {chunk_id} is not open")
        self._store.record_takeover_end(takeover_id=takeover_id, ended_at=self._clock.now())
