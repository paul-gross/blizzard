"""Draining a tenant's forge budget notices into its event log — ``forge-budget-low`` when a budget turned a caller
away, ``forge-rate-limited`` when the forge refused a call for its rate, each at most once per budget per reset window.
The flow driver runs it every tick, off the event loop; no budget lock is held while a notice is written."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.store.utc import iso_utc

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.forge.budget import BudgetNotice, BudgetNoticeKind, ForgeBudgets

_KINDS: dict[BudgetNoticeKind, EventLogKind] = {
    BudgetNoticeKind.BUDGET_LOW: "forge-budget-low",
    BudgetNoticeKind.RATE_LIMITED: "forge-rate-limited",
}


class BudgetNoticeDrain:
    """One tenant's budgets and the event log their notices are recorded in."""

    def __init__(self, budgets: ForgeBudgets, event_log: EventLogService) -> None:
        self._budgets = budgets
        self._event_log = event_log

    def drain(self, at: datetime) -> int:
        """Record every notice queued since the last drain, oldest first; answers how many it recorded."""
        notices = self._budgets.drain_notices()
        for notice in notices:
            self._event_log.record(
                kind=_KINDS[notice.kind],
                runner_id=None,
                chunk_id=None,
                project_id=None,
                lease_id=None,
                node_name=None,
                message=_message(notice),
                detail={
                    "forge": notice.key.forge_id,
                    "secret": notice.key.secret_name,
                    "bucket": notice.bucket,
                    "remaining": notice.remaining,
                    "limit": notice.limit,
                    "reset": iso_utc(notice.reset),
                },
                at=at,
            )
        return len(notices)


def _message(notice: BudgetNotice) -> str:
    key = f"forge {notice.key.forge_id} ({notice.key.secret_name or 'anonymous'}, {notice.bucket})"
    left = f"{notice.remaining}/{notice.limit} left" if notice.limit is not None else "no reading"
    until = iso_utc(notice.reset)
    match notice.kind:
        case BudgetNoticeKind.BUDGET_LOW:
            return f"{key}: rate budget low ({left}); lower-priority calls wait until {until}"
        case BudgetNoticeKind.RATE_LIMITED:
            return f"{key}: the forge refused a call for its rate ({left}); every call waits until {until}"
