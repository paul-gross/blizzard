"""The worker attach channel — ``blizzard runner attach --name <n>``.

A worker durably submits an explicit artifact for a ``produces:`` name, authorized by its spawn-minted
lease token; :meth:`AttachmentService.attach` is the one place the write happens. :func:`check_attachable`
accepts non-empty content against the active lease, even mid-judgement (newest wins until the outcome is
buffered), and refuses empty content, an open takeover's closed reference lease, and a lease whose outcome is
already buffered — nothing would publish any of those."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository
from blizzard.runner.leases.operator_requests import IWriteAttachmentRepository
from blizzard.runner.leases.worker_lease import WorkerLease, WorkerVerb

__all__ = [
    "AttachmentEmpty",
    "AttachmentOnClosedLease",
    "AttachmentRefused",
    "AttachmentService",
    "AttachmentTooLate",
    "check_attachable",
]


# The armed crash window (``bzh:crash-point-registry``): the attach row is
# durable but the ``200`` has not returned. Recovery owes nothing but durability.
_CP_ATTACH_AFTER_RECORD = crashpoint(
    "attach.after-record.before-response",
    "runner recorded the attachment durably but has not returned 200 — a kill -9 here must not lose it",
)


class AttachmentRefused(Exception):
    """Base for the attach refusals."""


class AttachmentEmpty(AttachmentRefused):
    """The attachment carries no content."""


class AttachmentOnClosedLease(AttachmentRefused):
    """The attach names the closed reference lease an open takeover holds."""


class AttachmentTooLate(AttachmentRefused):
    """The lease's outcome is already buffered for the hub; the declaration would never be submitted."""


def check_attachable(worker: WorkerLease, *, name: str, content: str, outcome_pending: bool) -> None:
    """Pass when ``content`` may be staged under ``name`` for ``worker``'s lease, else raise
    the :class:`AttachmentRefused` that applies — empty content first.
    ``outcome_pending`` is whether the lease's outcome is already buffered for the hub."""
    if not content:
        raise AttachmentEmpty(f"attachment {name!r} is empty — an attachment must carry content")
    if not worker.accepts(WorkerVerb.ATTACH):
        raise AttachmentOnClosedLease(
            f"lease {worker.lease.lease_id} is closed — "
            "an attachment on a takeover's reference lease is never published"
        )
    if outcome_pending:
        raise AttachmentTooLate(
            f"lease {worker.lease.lease_id}'s outcome is already submitted — "
            "declare assets before the node-step's verdict"
        )


class AttachmentService:
    """Composition-root-wired: the attachment store, the outbound buffer read, and the clock."""

    def __init__(self, store: IWriteAttachmentRepository, clock: IClock, outbound: IReadOutboundRepository) -> None:
        self._store = store
        self._outbound = outbound
        self._clock = clock

    def attach(self, worker: WorkerLease, *, name: str, content: str) -> None:
        """Record ``content`` under ``name`` for ``worker``'s lease, or raise the
        :class:`AttachmentRefused` :func:`check_attachable` names. ``worker`` is already
        resolved and its token checked by the caller (``bzh:domain-takes-objects``).
        Append-and-read-newest: a repeat call for the same ``(lease, name)`` is a
        correction, not an error."""
        lease = worker.lease
        check_attachable(
            worker,
            name=name,
            content=content,
            outcome_pending=lease.lease_id in self._outbound.pending_submission_lease_ids(),
        )
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
