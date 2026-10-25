"""The close-intent drain sweep: retires pending ``close_intents`` rows, a bounded number per pass,
unconditionally like the event-derivation sweep. Dependency-free
(``bzh:domain-core``): every collaborator is an injected Protocol, so :meth:`sweep` is one
complete, directly-callable step (``bzh:steppable-loop``); ground is
``blizzard-context:/architecture/crash-correctness/hub.md``'s own."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from structlog.contextvars import bound_contextvars

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.lane_retry import backoff_delay
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.delivery_read import DeliverySources, DeliveryTrace, board_chunk_url
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import PendingCloseIntent, WorkItemCloseOutcome
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant
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

#: The outcomes that retire an intent.
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


def close_event(label: str, outcome: WorkItemCloseOutcome, reason: str | None) -> CloseEvent:
    """A closed ref announces ``work-item-closed``; a gone or failed one announces
    ``work-item-close-failed`` with its outcome and reason. ``label`` is the ref as its source displays it now."""
    if outcome is WorkItemCloseOutcome.CLOSED:
        return CloseEvent(kind=_EVENT_CLOSED, message=f"closed {label}", detail=None)
    return CloseEvent(
        kind=_EVENT_CLOSE_FAILED,
        message=f"failed to close {label}: {reason}",
        detail={"outcome": outcome.value, "reason": reason},
    )


@domain_model
@dataclass(frozen=True)
class TenantCloseIntent:
    """One row of the close drain's hub-wide corpus: a pending intent and the tenant it belongs to."""

    tenant: TenantId
    intent: PendingCloseIntent


class IReadCloseIntentCorpus(Protocol):
    """The close-drain sweep's corpus, read across every open tenant."""

    def pending_close_intents(self) -> Sequence[TenantCloseIntent]:
        """Every open tenant's pending intent whose chunk is neither grouped away nor deleted, with
        its attempt history, in intent order; due-ness is the drainer's call."""
        ...


class CloseIntentDrainer:
    """One tenant's half of the close drain: retires the due intents a pass hands it through each
    ref's own source binding, recording each attempt's outcome. Its ``delivery``, ``artifacts``,
    ``events`` and ``work_sources`` all serve that one tenant."""

    def __init__(
        self,
        *,
        delivery: IWriteChunkDeliveryRepository,
        artifacts: IReadChunkArtifactsRepository,
        chunks: IReadChunkRecordRepository,
        events: EventLogService,
        work_sources: IWorkSourceRegistry,
        clock: IClock,
        public_url: str | None = None,
    ) -> None:
        self._delivery = delivery
        self._artifacts = artifacts
        self._public_url = public_url
        self._chunks = chunks
        self._events = events
        self._work_sources = work_sources
        self._clock = clock

    def _traces(self, chunk_ids: set[str]) -> dict[str, DeliveryTrace | None]:
        """One batched delivery read for the tenant's share of the pass (``bzh:bulk-reconstitution``)."""
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

    def _projects(self, chunk_ids: set[str]) -> dict[str, ProjectId]:
        """The project of each of the pass's chunks, in one batched read (``bzh:bulk-reconstitution``); a chunk the
        record no longer holds is absent."""
        if not chunk_ids:
            return {}
        chunks = self._chunks.get_many(sorted(chunk_ids))
        return {chunk_id: chunk.project_id for chunk_id, chunk in chunks.items() if chunk.project_id is not None}

    def drain(self, due: Sequence[PendingCloseIntent], tally: CloseDrainTally) -> None:
        """Attempt each of ``due`` — this tenant's due intents, in intent order — counting each
        outcome into ``tally``. A per-ref failure is caught and counted rather than raised: a
        ``gone`` or ``failed`` outcome is itself an informative result."""
        chunk_ids = {intent.chunk_id for intent in due}
        traces = self._traces(chunk_ids)
        projects = self._projects(chunk_ids)
        for intent in due:
            closer = self._work_sources.closer(intent.ref.source)
            if closer is None:
                tally.skipped += 1
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
            tally.count(outcome)
            _CP_CLOSE_AFTER_CLOSE_BEFORE_RECORD.reached()
            wrote = self._delivery.record_work_item_closure(
                intent.chunk_id, pointer=intent.ref, outcome=outcome, reason=reason, at=at
            )  # retires the intent too, in the same transaction, when the outcome is terminal
            if not wrote:
                continue  # a redelivered sweep already recorded this outcome
            event = close_event(self._work_sources.label(intent.ref), outcome, reason)
            self._events.record(
                kind=event.kind,
                runner_id=None,
                chunk_id=intent.chunk_id,
                project_id=projects.get(intent.chunk_id),
                lease_id=None,
                node_name=None,
                message=event.message,
                detail=event.detail,
                at=at,
            )


@domain_model
@dataclass
class CloseDrainTally:
    """One drain pass's outcome counts, across every tenant it acted in."""

    closed: int = 0
    gone: int = 0
    failed: int = 0
    skipped: int = 0

    def count(self, outcome: WorkItemCloseOutcome) -> None:
        if outcome is WorkItemCloseOutcome.CLOSED:
            self.closed += 1
        elif outcome is WorkItemCloseOutcome.GONE:
            self.gone += 1
        else:
            self.failed += 1


def fair_share(
    due: Mapping[TenantId, Sequence[PendingCloseIntent]], limit: int, start: int
) -> dict[TenantId, list[PendingCloseIntent]]:
    """Up to ``limit`` of ``due``, one intent per tenant in turn from the ``start``-th tenant by name."""
    tenants = sorted(due)
    if tenants:
        start %= len(tenants)
        tenants = tenants[start:] + tenants[:start]
    taken: dict[TenantId, list[PendingCloseIntent]] = {}
    depth = 0
    while sum(map(len, taken.values())) < limit:
        round_ = [tenant for tenant in tenants if len(due[tenant]) > depth]
        if not round_:
            break
        for tenant in round_[: limit - sum(map(len, taken.values()))]:
            taken.setdefault(tenant, []).append(due[tenant][depth])
        depth += 1
    return taken


class CloseDrainSweep:
    """The close drain's one pass per hub: reads every open tenant's pending intents once, keeps up to
    ``CLOSE_DRAIN_PASS_LIMIT`` due ones by :func:`fair_share`, and hands each tenant its share through
    its own :class:`CloseIntentDrainer`, so no intent is acted on in another tenant's scope and one
    tenant's burst or fault never changes another's share."""

    def __init__(
        self,
        *,
        corpus: IReadCloseIntentCorpus,
        tenant: Callable[[TenantId], CloseIntentDrainer],
        clock: IClock,
    ) -> None:
        self._corpus = corpus
        self._tenant = tenant
        self._clock = clock
        self._rotation = 0

    def sweep(self) -> None:
        """One drain pass over up to ``CLOSE_DRAIN_PASS_LIMIT`` due intents; a closed tenant is skipped and a
        fault in one tenant is caught there."""
        now = self._clock.now()
        due: dict[TenantId, list[PendingCloseIntent]] = {}
        for row in self._corpus.pending_close_intents():
            if close_intent_is_due(
                now, attempt_count=row.intent.attempt_count, last_attempt_at=row.intent.last_attempt_at
            ):
                due.setdefault(row.tenant, []).append(row.intent)
        by_tenant = fair_share(due, CLOSE_DRAIN_PASS_LIMIT, self._rotation)
        self._rotation += 1
        tally = CloseDrainTally()
        failed: list[TenantId] = []
        for tenant, intents in by_tenant.items():
            with bound_contextvars(tenant_id=tenant):
                try:
                    self._tenant(tenant).drain(intents, tally)
                except (TenantClosed, UnknownTenant):
                    _log.info("close intent drain skipped a closed tenant", intents=len(intents))
                except Exception as exc:
                    failed.append(tenant)
                    _log.warning("close intent drain failed for a tenant", fault=repr(exc))
        _log.info(
            "close intent drain sweep completed",
            closed=tally.closed,
            gone=tally.gone,
            failed=tally.failed,
            skipped=tally.skipped,
            tenants=len(by_tenant),
            tenants_failed=sorted(failed),
        )
