"""The close-intent drain sweep (blizzard#383): retires every pending ``close_intents`` row,
unconditionally like the event-derivation and delivery-materialization sweeps. Dependency-free
(``bzh:domain-core``): every collaborator is an injected Protocol, so :meth:`sweep` is one
complete, directly-callable step (``bzh:steppable-loop``); ground is
``blizzard-context:/architecture/crash-correctness/hub.md``'s own."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunks.events import IWriteChunkEventsRepository
from blizzard.hub.domain.work import WorkItemCloseOutcome
from blizzard.hub.work_sources.closer import WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.work_closure")

# The close-intent outbox's second window (blizzard#383) — the forge close returned but the
# atomic record-and-retire write hasn't landed yet; recovered by a re-attempt next pass.
_CP_CLOSE_AFTER_CLOSE_BEFORE_RECORD = crashpoint(
    "close.after-close.before-record",
    "the close attempt returned; its outcome is not yet recorded and the intent is not yet retired",
)

_HUB_RUNNER_ID = "hub"

_EVENT_CLOSED = "work-item-closed"
_EVENT_CLOSE_FAILED = "work-item-close-failed"


class CloseIntentDrainer:
    """Per pending close intent: retire it through its ref's own source binding,
    recording the attempt's outcome."""

    def __init__(
        self,
        *,
        delivery: IWriteChunkDeliveryRepository,
        events: IWriteChunkEventsRepository,
        work_sources: IWorkSourceRegistry,
        clock: IClock,
    ) -> None:
        self._delivery = delivery
        self._events = events
        self._work_sources = work_sources
        self._clock = clock

    def sweep(self) -> None:
        """One complete drain pass over every pending intent. A per-ref failure is caught
        and counted rather than raised — a ``gone`` or ``failed`` outcome is itself an
        informative result. One aggregate INFO summary per pass (``bzh:structlog-logging``)."""
        closed = gone = failed = skipped = 0
        for intent in self._delivery.pending_close_intents():
            closer = self._work_sources.closer(intent.ref.source)
            if closer is None:
                skipped += 1
                continue  # D4: no closer bound for this source today — stays pending
            at = self._clock.now()
            try:
                closer.close(intent.ref)
                outcome, reason = WorkItemCloseOutcome.CLOSED, None
            except WorkItemGoneError as exc:
                outcome, reason = WorkItemCloseOutcome.GONE, str(exc)
            except WorkCloseError as exc:
                outcome, reason = WorkItemCloseOutcome.FAILED, str(exc)
            if outcome is WorkItemCloseOutcome.CLOSED:
                closed += 1
            elif outcome is WorkItemCloseOutcome.GONE:
                gone += 1
            else:
                failed += 1
            _CP_CLOSE_AFTER_CLOSE_BEFORE_RECORD.reached()
            wrote = self._delivery.record_work_item_closure(
                intent.chunk_id, pointer=intent.ref, outcome=outcome, reason=reason, at=at
            )  # retires the intent too, in the same transaction, when the outcome is terminal
            if not wrote:
                continue  # a redelivered sweep already recorded this outcome
            if outcome is WorkItemCloseOutcome.CLOSED:
                self._events.record_event(
                    severity="info",
                    kind=_EVENT_CLOSED,
                    runner_id=_HUB_RUNNER_ID,
                    chunk_id=intent.chunk_id,
                    lease_id=None,
                    node_name=None,
                    message=f"closed {intent.ref.source}#{intent.ref.ref}",
                    detail=None,
                    at=at,
                )
            else:
                self._events.record_event(
                    severity="warning",
                    kind=_EVENT_CLOSE_FAILED,
                    runner_id=_HUB_RUNNER_ID,
                    chunk_id=intent.chunk_id,
                    lease_id=None,
                    node_name=None,
                    message=f"failed to close {intent.ref.source}#{intent.ref.ref}: {reason}",
                    detail={"outcome": outcome.value, "reason": reason},
                    at=at,
                )
        _log.info("close intent drain sweep completed", closed=closed, gone=gone, failed=failed, skipped=skipped)
