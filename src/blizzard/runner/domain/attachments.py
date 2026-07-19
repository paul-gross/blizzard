"""The worker attach channel — ``blizzard runner attach --name <n>`` (issue #113, Phase 2).

Behind ``POST /api/leases/{lease_id}/attachments``: a worker durably submits an
explicit artifact for a ``produces:`` name, authorized by the lease token minted at
its own spawn (Phase 1). :meth:`AttachmentService.attach` is the one place the write
happens (``bzh:controller-read-only`` — the API edge resolves the lease to an object
and delegates here rather than writing through a store it holds itself, mirroring
:class:`~blizzard.runner.domain.takeover.TakeoverService` /
:class:`~blizzard.runner.domain.requeue.RequeueService`).

Nothing yet reads an attachment back to prefer it over the judgement assessment —
that is completion assembly's own rewrite (Phase 3). This phase only makes the
attach durable, single-transaction (``runner/store/internal/sqlalchemy_store.py``'s
``record_attachment``) so it survives a ``kill -9`` between the attach and whatever
submission would otherwise read it back.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.domain.lease_auth import check_lease_token
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord

__all__ = ["AttachmentRejected", "AttachmentService"]

# The dangerous window criterion 3 names (issue #113): the attach row is durable — its
# single committed txn (``record_attachment``) has returned — but the ``200`` has not, so
# a ``kill -9`` here is exactly "a runner dies between the attach and whatever submission
# would read it back". Recovery owes nothing but durability: the row is on disk, and a
# later completion (Phase 3) / the recovering ADVANCE tick re-derives it via
# ``attachments_for_lease``. Swept by ``tests/crash/test_kill9_sweep.py::
# test_kill9_at_attach_crash_point`` (``bzh:crash-point-registry``); unarmed, ``reached()``
# is one module-global compare.
_CP_ATTACH_AFTER_RECORD = crashpoint(
    "attach.after-record.before-response",
    "runner recorded the attachment durably but has not returned 200 — a kill -9 here must not lose it",
)


class AttachmentRejected(Exception):
    """The presented lease token does not authorize this attach — the API edge maps
    this to ``403``."""


class AttachmentService:
    """Composition-root-wired: the write store and clock (issue #113, Phase 2)."""

    def __init__(self, store: IWriteRunnerStore, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def attach(self, lease: LeaseRecord, *, presented_token: str | None, name: str, content: str) -> None:
        """Record ``content`` under ``name`` for ``lease``, or raise
        :class:`AttachmentRejected` if ``presented_token`` does not authorize it.

        ``lease`` is already resolved by the caller (``bzh:domain-takes-objects``) —
        this never looks a lease id up itself. Append-and-read-newest
        (``bzh:facts-not-status``): a repeat call for the same ``(lease, name)`` is a
        correction, not an error."""
        stored_hash = self._store.lease_token_hash(lease.lease_id)
        if not check_lease_token(presented_token=presented_token, stored_hash=stored_hash):
            raise AttachmentRejected(f"presented token does not authorize lease {lease.lease_id}")
        self._store.record_attachment(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            name=name,
            content=content,
            attached_at=self._clock.now(),
        )
        # The row is durable (the txn above committed) but the caller has not yet returned
        # the 200 — criterion 3's kill-9 window (armed only under the crash sweep).
        _CP_ATTACH_AFTER_RECORD.reached()
