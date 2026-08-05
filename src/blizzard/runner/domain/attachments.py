"""The worker attach channel — ``blizzard runner attach --name <n>`` (issue #113, Phase 2).

A worker durably submits an explicit artifact for a ``produces:`` name, authorized by
the lease token minted at its own spawn. :meth:`AttachmentService.attach` is the one
place the write happens (``bzh:controller-read-only``)."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.domain.lease_auth import check_lease_token
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord

__all__ = ["AttachmentRejected", "AttachmentService"]

# The armed crash window (issue #113, ``bzh:crash-point-registry``): the attach row is
# durable but the ``200`` has not returned. Recovery owes nothing but durability.
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
        :class:`AttachmentRejected` if ``presented_token`` does not authorize it. ``lease``
        is already resolved by the caller (``bzh:domain-takes-objects``). Append-and-read-
        newest: a repeat call for the same ``(lease, name)`` is a correction, not an error."""
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
        _CP_ATTACH_AFTER_RECORD.reached()
