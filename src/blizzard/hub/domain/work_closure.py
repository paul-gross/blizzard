"""The delivery closure sweep — closing a delivered chunk's work items (issue #216): the **guarantee**
half of the two-part close, idempotent against the opportunistic hint having already fired.

Dependency-free (``bzh:domain-core``): every collaborator is an injected Protocol, so :meth:`sweep` is
one complete, directly-callable step (``bzh:steppable-loop``). A per-ref failure is recorded as a
``failed`` fact and retried next sweep (``blizzard-context:/architecture/crash-correctness.md``)."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import IWriteChunkRepository, WorkItemCloseOutcome
from blizzard.hub.work_sources.closer import WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.work_closure")

# The hub coordinator's own reserved `event_log.runner_id` for a hub-originated event; kept private
# here because this sweep owns its own event-recording concern (`bzh:repository-split`).
_HUB_RUNNER_ID = "hub"

_EVENT_CLOSED = "work-item-closed"
_EVENT_CLOSE_FAILED = "work-item-close-failed"


class DeliveryClosureReconciler:
    """Per opted-in, close-capable work source: close every landed chunk's still-open
    work refs through that source's own binding, recording each attempt's outcome."""

    def __init__(self, *, chunks: IWriteChunkRepository, work_sources: IWorkSourceRegistry, clock: IClock) -> None:
        self._chunks = chunks
        self._work_sources = work_sources
        self._clock = clock

    def sweep(self) -> None:
        """One complete reconciliation pass over every opted-in, close-capable source.

        Candidates are computed once, then filtered per source so a closer only ever sees its own refs.
        A per-ref failure is caught and counted rather than raised — a ``gone`` or ``failed`` outcome is
        itself an informative result. One aggregate INFO summary per pass (``bzh:structlog-logging``)."""
        candidates = self._chunks.closable_work_refs()
        closed = gone = failed = 0
        for name in self._work_sources.closing_names():
            closer = self._work_sources.closer(name)
            if closer is None:  # pragma: no cover - closing_names() only names built ones
                continue
            for candidate in candidates:
                if candidate.ref.source != name:
                    continue
                at = self._clock.now()
                try:
                    closer.close(candidate.ref)
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
                wrote = self._chunks.record_work_item_closure(
                    candidate.chunk_id, pointer=candidate.ref, outcome=outcome, reason=reason, at=at
                )
                if not wrote:
                    continue  # a redelivered sweep already recorded this outcome
                if outcome is WorkItemCloseOutcome.CLOSED:
                    self._chunks.record_event(
                        severity="info",
                        kind=_EVENT_CLOSED,
                        runner_id=_HUB_RUNNER_ID,
                        chunk_id=candidate.chunk_id,
                        lease_id=None,
                        node_name=None,
                        message=f"closed {candidate.ref.source}#{candidate.ref.ref}",
                        detail=None,
                        at=at,
                    )
                else:
                    self._chunks.record_event(
                        severity="warning",
                        kind=_EVENT_CLOSE_FAILED,
                        runner_id=_HUB_RUNNER_ID,
                        chunk_id=candidate.chunk_id,
                        lease_id=None,
                        node_name=None,
                        message=f"failed to close {candidate.ref.source}#{candidate.ref.ref}: {reason}",
                        detail={"outcome": outcome.value, "reason": reason},
                        at=at,
                    )
        _log.info("delivery closure sweep completed", closed=closed, gone=gone, failed=failed)
