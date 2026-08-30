"""The worker attach channel — ``blizzard runner attach --name <n>`` (issue #113, Phase 2).

A worker durably submits an explicit artifact for a ``produces:`` name, authorized by
the lease token minted at its own spawn. :meth:`AttachmentService.attach` is the one
place the write happens (``bzh:controller-read-only``)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.auth.tokens import IReadTokenRepository
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.domain.leases import LeaseRecord

__all__ = ["AttachmentRejected", "AttachmentService", "IReadAttachmentRepository", "IWriteAttachmentRepository"]


class IReadAttachmentRepository(Protocol):
    """Read-only attachment queries (held by read-path edges)."""

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        """The lease's explicit artifact submissions, newest content per ``name``
        (issue #113). Append-only, latest-wins-per-``(lease_id, name)``: a re-attach of
        the same name reads back as the replacement, never a duplicate."""
        ...


class IWriteAttachmentRepository(IReadAttachmentRepository, Protocol):
    """Read-write attachment store — held only by the domain."""

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
    """Composition-root-wired: the attachment store, the token store (for authorization),
    and the clock (issue #113, Phase 2)."""

    def __init__(self, store: IWriteAttachmentRepository, clock: IClock, *, tokens: IReadTokenRepository) -> None:
        self._store = store
        self._clock = clock
        self._tokens = tokens

    def attach(self, lease: LeaseRecord, *, presented_token: str | None, name: str, content: str) -> None:
        """Record ``content`` under ``name`` for ``lease``, or raise
        :class:`AttachmentRejected` if ``presented_token`` does not authorize it. ``lease``
        is already resolved by the caller (``bzh:domain-takes-objects``). Append-and-read-
        newest: a repeat call for the same ``(lease, name)`` is a correction, not an error."""
        stored_hash = self._tokens.lease_token_hash(lease.lease_id)
        if not LeaseToken(presented_token, stored_hash).valid:
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
