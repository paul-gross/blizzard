"""The delivery closure sweep — closing a delivered chunk's work items (issue #216).

Part 2 of the two-half close: a worker's commit metadata (the build preamble) is the
opportunistic hint that fires forge auto-close on a fast-forward landing with no hub
involvement at all; this reconciler is the **guarantee**, catch-all and idempotent
against that hint having already fired. Per opted-in, close-capable work source,
:class:`DeliveryClosureReconciler` reads every landed, non-grouped chunk's still-open
work refs (:meth:`~blizzard.hub.domain.work.IReadChunkRepository.closable_work_refs`
— ``has_landed_repos`` is the sole landing gate, not chunk status), attempts to close
each through its own source binding, and records the outcome durably.

Dependency-free (``bzh:domain-core``): every collaborator is an injected Protocol
(:class:`~blizzard.hub.domain.work.IWriteChunkRepository`,
:class:`~blizzard.hub.work_sources.source.IWorkSourceRegistry`,
:class:`~blizzard.foundation.clock.IClock`), so :meth:`~DeliveryClosureReconciler.sweep`
is a single, complete, directly-callable step (``bzh:steppable-loop``) — the background
driver (``blizzard.hub.app``) is a thin sleep-and-call wrapper around it, the same
shape :class:`~blizzard.hub.domain.forge_status.AnnotationReconciler` already
establishes. A per-ref failure is caught, recorded as a ``failed`` fact, and retried on
the next sweep — nothing raises past :meth:`sweep`. A mid-sweep crash loses nothing
durable: the next pass re-derives the same candidate set from
:meth:`~blizzard.hub.domain.work.IReadChunkRepository.closable_work_refs` and re-issues
an idempotent close (see ``blizzard-context:/architecture/crash-correctness.md``'s
recorded exemption for this sweep).
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import IWriteChunkRepository, WorkItemCloseOutcome
from blizzard.hub.work_sources.closer import WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.work_closure")

# The hub coordinator's own reserved `event_log.runner_id` for a hub-originated event —
# mirrors `delivery.hub_node._HUB_RUNNER_ID` (kept private there too; this sweep owns
# its own event-recording concern, `bzh:repository-split`).
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

        Candidates are computed once (``closable_work_refs``), then filtered per
        source so a closer only ever sees its own refs; a source with no closer
        bound is skipped entirely (``closing_names()`` never names it). A per-ref
        failure is caught and counted rather than raised — a ``gone`` or ``failed``
        outcome is itself a successful, informative sweep result, not an error the
        sweep needs to surface via an exception. One aggregate INFO summary is
        emitted per pass, win or lose (``bzh:structlog-logging``)."""
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
