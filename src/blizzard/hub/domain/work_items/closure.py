"""The close-intent drain sweep: retires pending ``close_intents`` rows, a bounded number per pass,
unconditionally like the event-derivation sweep. Dependency-free
(``bzh:domain-core``): every collaborator is an injected Protocol, so :meth:`sweep` is one
complete, directly-callable step (``bzh:steppable-loop``); ground is
``blizzard-context:/architecture/crash-correctness/hub.md``'s own."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.lane_retry import backoff_delay
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.delivery_read import DeliverySources, DeliveryTrace, board_chunk_url
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.work_sources.closer import WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.work_closure")

# The close-intent outbox's second window — the forge close returned but the
# atomic record-and-retire write hasn't landed yet; recovered by a re-attempt next pass.
_CP_CLOSE_AFTER_CLOSE_BEFORE_RECORD = crashpoint(
    "close.after-close.before-record",
    "the close attempt returned; its outcome is not yet recorded and the intent is not yet retired",
)

_EVENT_CLOSED: EventLogKind = "work-item-closed"
_EVENT_CLOSE_FAILED: EventLogKind = "work-item-close-failed"

#: The close-drain sweep's own backoff base — pinned equal to
#: ``CLOSE_DRAIN_INTERVAL_SECONDS`` in ``blizzard.hub.app`` by a dedicated test.
CLOSE_DRAIN_BACKOFF_BASE_SECONDS = 60
#: The backoff's cap — never wait longer than this between due-checks of the same intent.
CLOSE_DRAIN_BACKOFF_CAP_SECONDS = 3600
#: The most due intents one pass attempts — the rest wait for the next pass, in the read's order.
CLOSE_DRAIN_PASS_LIMIT = 100


def close_intent_is_due(now: datetime, *, attempt_count: int | None, last_attempt_at: datetime | None) -> bool:
    """Due with no prior attempt at all; otherwise due once
    ``backoff_delay(n, base, cap)`` has passed since the last one — a pure domain
    rule (``bzh:domain-core``), applied by ``CloseIntentDrainer.sweep`` over each pending intent's history."""
    if not attempt_count or last_attempt_at is None:
        return True
    threshold = backoff_delay(
        attempt_count,
        timedelta(seconds=CLOSE_DRAIN_BACKOFF_BASE_SECONDS),
        timedelta(seconds=CLOSE_DRAIN_BACKOFF_CAP_SECONDS),
    )
    return now - last_attempt_at >= threshold


class CloseIntentState(StrEnum):
    """A close intent's lifecycle. Enqueued ``pending``; a skipped attempt (no closer bound
    for the source) or a ``failed`` one leaves it ``backing-off``, re-attempted once due; a
    terminal outcome ``retired`` it, never re-attempted and never re-enqueued for that ref."""

    PENDING = "pending"
    BACKING_OFF = "backing-off"
    RETIRED = "retired"

    @classmethod
    def after(cls, outcome: WorkItemCloseOutcome) -> CloseIntentState:
        """The state one attempt's ``outcome`` leaves its intent in."""
        return _STATE_AFTER[outcome]


_STATE_AFTER: Mapping[WorkItemCloseOutcome, CloseIntentState] = {
    WorkItemCloseOutcome.CLOSED: CloseIntentState.RETIRED,
    WorkItemCloseOutcome.GONE: CloseIntentState.RETIRED,
    WorkItemCloseOutcome.FAILED: CloseIntentState.BACKING_OFF,
}

#: The outcomes that retire an intent, filtered on by the store's retire and re-enqueue guards.
TERMINAL_CLOSE_OUTCOMES: frozenset[WorkItemCloseOutcome] = frozenset(
    outcome for outcome in WorkItemCloseOutcome if CloseIntentState.after(outcome) is CloseIntentState.RETIRED
)


@domain_model
@dataclass(frozen=True)
class CloseEvent:
    """The event-log entry one recorded close outcome announces."""

    kind: EventLogKind
    message: str
    detail: dict[str, str | None] | None


def close_event(ref: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None) -> CloseEvent:
    """A closed ref announces ``work-item-closed``; a gone or failed one announces
    ``work-item-close-failed`` with its outcome and reason."""
    if outcome is WorkItemCloseOutcome.CLOSED:
        return CloseEvent(kind=_EVENT_CLOSED, message=f"closed {ref.source}#{ref.ref}", detail=None)
    return CloseEvent(
        kind=_EVENT_CLOSE_FAILED,
        message=f"failed to close {ref.source}#{ref.ref}: {reason}",
        detail={"outcome": outcome.value, "reason": reason},
    )


class CloseIntentDrainer:
    """Per pending close intent: retire it through its ref's own source binding,
    recording the attempt's outcome."""

    def __init__(
        self,
        *,
        delivery: IWriteChunkDeliveryRepository,
        artifacts: IReadChunkArtifactsRepository,
        events: EventLogService,
        work_sources: IWorkSourceRegistry,
        clock: IClock,
        public_url: str | None = None,
    ) -> None:
        self._delivery = delivery
        self._artifacts = artifacts
        self._public_url = public_url
        self._events = events
        self._work_sources = work_sources
        self._clock = clock

    def _traces(self, chunk_ids: set[str]) -> dict[str, DeliveryTrace | None]:
        """One batched delivery read for the whole pass (``bzh:bulk-reconstitution``)."""
        if not chunk_ids:
            return {}
        sources = self._artifacts.delivery_sources_for(sorted(chunk_ids))
        return {
            chunk_id: DeliveryTrace.of(
                chunk_id,
                sources.get(chunk_id, DeliverySources()),
                board_url=board_chunk_url(self._public_url, chunk_id),
            )
            for chunk_id in chunk_ids
        }

    def sweep(self) -> None:
        """One complete drain pass over up to ``CLOSE_DRAIN_PASS_LIMIT`` due intents. A per-ref failure is caught
        and counted rather than raised — a ``gone`` or ``failed`` outcome is itself an
        informative result. One aggregate INFO summary per pass (``bzh:structlog-logging``)."""
        closed = gone = failed = skipped = 0
        now = self._clock.now()
        due = [
            intent
            for intent in self._delivery.pending_close_intents()
            if close_intent_is_due(now, attempt_count=intent.attempt_count, last_attempt_at=intent.last_attempt_at)
        ][:CLOSE_DRAIN_PASS_LIMIT]
        traces = self._traces({intent.chunk_id for intent in due})
        for intent in due:
            closer = self._work_sources.closer(intent.ref.source)
            if closer is None:
                skipped += 1
                # No closer bound for this source today — stays pending, ticking the
                # backoff clock so it isn't reconsidered every sweep.
                self._delivery.record_close_attempt_skipped(intent.intent_id, at=self._clock.now())
                continue
            at = self._clock.now()
            try:
                closer.close(intent.ref, trace=traces.get(intent.chunk_id))
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
            event = close_event(intent.ref, outcome, reason)
            self._events.record(
                kind=event.kind,
                runner_id=None,
                chunk_id=intent.chunk_id,
                lease_id=None,
                node_name=None,
                message=event.message,
                detail=event.detail,
                at=at,
            )
        _log.info("close intent drain sweep completed", closed=closed, gone=gone, failed=failed, skipped=skipped)
