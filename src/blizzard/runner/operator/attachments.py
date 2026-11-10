"""The worker attach channel — ``blizzard runner attach --name <n>``.

A worker durably submits an explicit artifact for a ``produces:`` name, authorized by its spawn-minted
lease token; :meth:`AttachmentService.attach` is the one place the write happens. :func:`check_attachable`
accepts non-empty content against the active lease, even mid-judgement (newest wins until the outcome is
buffered, acked or not), and refuses empty content, an open takeover's closed reference lease, and a lease whose
outcome is already buffered — nothing would publish any of those."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import HubClientError, IArtifactChecker
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository
from blizzard.runner.leases.lease_lock import IWriteLeaseLock
from blizzard.runner.leases.operator_requests import IWriteAttachmentRepository
from blizzard.runner.leases.worker_lease import WorkerLease, WorkerVerb

__all__ = [
    "AttachmentEmpty",
    "AttachmentFormatRefused",
    "AttachmentOnClosedLease",
    "AttachmentRefused",
    "AttachmentService",
    "AttachmentTooLate",
    "check_attachable",
]


_log = get_logger(__name__)

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


class AttachmentFormatRefused(AttachmentRefused):
    """The hub refuses the content for the format its graph owes the name; the message is the hub's refusal."""


class AttachmentOnClosedLease(AttachmentRefused):
    """The attach names the closed reference lease an open takeover holds."""


class AttachmentTooLate(AttachmentRefused):
    """The lease's outcome is already buffered for the hub; the declaration would never be submitted."""


def check_attachable(worker: WorkerLease, *, name: str, content: str, settled: bool) -> None:
    """Pass when ``content`` may be staged under ``name`` for ``worker``'s lease, else raise
    the :class:`AttachmentRefused` that applies — empty content first.
    ``settled`` is whether the lease's outcome is already buffered for the hub, acked or not."""
    if not content:
        raise AttachmentEmpty(f"attachment {name!r} is empty — an attachment must carry content")
    if not worker.accepts(WorkerVerb.ATTACH):
        raise AttachmentOnClosedLease(
            f"lease {worker.lease.lease_id} is closed — "
            "an attachment on a takeover's reference lease is never published"
        )
    if settled:
        raise AttachmentTooLate(
            f"lease {worker.lease.lease_id}'s outcome is already submitted — "
            "declare assets before the node-step's verdict"
        )


class AttachmentService:
    """Composition-root-wired: the attachment store, the outbound buffer read, the lease lock, the clock, and the
    hub's stage-time format check."""

    def __init__(
        self,
        store: IWriteAttachmentRepository,
        clock: IClock,
        outbound: IReadOutboundRepository,
        lease_lock: IWriteLeaseLock,
        *,
        stage_check: IArtifactChecker,
    ) -> None:
        self._store = store
        self._lease_lock = lease_lock
        self._outbound = outbound
        self._clock = clock
        self._stage_check = stage_check

    def _format_refusal(self, chunk_id: str, name: str, content: str) -> str | None:
        """The hub's refusal of ``content`` for staging, or ``None`` when it passes — and when there is no
        usable advisory verdict (transport failure or timeout, an old hub's 404, an upstream auth or identity
        refusal, a malformed answer): staging is a durable runner-local save, not hub acceptance, and delivery
        stays authoritative."""
        try:
            return self._stage_check.check_artifact(chunk_id, name, content)
        except HubClientError as exc:
            _log.warning("stage-time artifact check unavailable; staging unchecked", chunk_id=chunk_id, error=str(exc))
            return None

    def attach(self, worker: WorkerLease, *, name: str, content: str) -> None:
        """Record ``content`` under ``name`` for ``worker``'s lease, or raise the
        :class:`AttachmentRefused` that applies. ``worker`` is already
        resolved and its token checked by the caller (``bzh:domain-takes-objects``).
        Order: the lifecycle pre-check (:func:`check_attachable`, unlocked, so its refusals take precedence and
        cost no hub call), then the hub's format check outside the lock (:class:`AttachmentFormatRefused`), then the
        locked re-check and write.
        Append-and-read-newest: a repeat call for the same ``(lease, name)`` is a
        correction, not an error."""
        lease = worker.lease
        check_attachable(
            worker,
            name=name,
            content=content,
            settled=self._outbound.submission_buffered(lease.lease_id),
        )
        refusal = self._format_refusal(lease.chunk_id, name, content)
        if refusal is not None:
            raise AttachmentFormatRefused(refusal)
        # The settled fact is read under the lease's lock, in the unit that inserts: a capture of the outcome either
        # commits first and refuses this attachment, or commits after it and carries it.
        with self._lease_lock.locked(lease.lease_id) as unit:
            check_attachable(
                worker,
                name=name,
                content=content,
                settled=self._outbound.submission_buffered(lease.lease_id, unit=unit),
            )
            self._store.record_attachment(
                lease_id=lease.lease_id,
                chunk_id=lease.chunk_id,
                node_id=lease.node_id,
                epoch=lease.epoch,
                name=name,
                content=content,
                attached_at=self._clock.now(),
                unit=unit,
            )
        _CP_ATTACH_AFTER_RECORD.reached()
