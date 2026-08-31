"""SQLAlchemy adapter for the garden-trend read seam (package-private, blizzard#394
Phase 4).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the window
itself is bound in SQL, but period bucketing is left to `domain/garden_trend.py`'s
`compute_trend` (D6, ``bzh:sql-portable``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.hub.domain.garden_trend import TREND_FACT_KINDS, IReadGardenTrendRepository, TrendFact
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import finding_facts, findings


class GardenTrendStore:
    """Read-only garden-trend adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def facts_for_trend(self, routine_name: str, *, since: datetime, until: datetime) -> list[TrendFact]:
        with self._store.read("facts_for_trend") as conn:
            rows = conn.execute(
                select(finding_facts.c.kind, finding_facts.c.recorded_at, findings.c.introduced_at)
                .select_from(finding_facts.join(findings, finding_facts.c.finding_id == findings.c.finding_id))
                .where(
                    findings.c.routine_name == routine_name,
                    finding_facts.c.kind.in_(TREND_FACT_KINDS),
                    finding_facts.c.recorded_at >= since,
                    finding_facts.c.recorded_at < until,
                )
            ).all()
        return [TrendFact(kind=row.kind, recorded_at=row.recorded_at, introduced_at=row.introduced_at) for row in rows]


def _conforms_garden_trend_store(x: GardenTrendStore) -> IReadGardenTrendRepository:
    return x
