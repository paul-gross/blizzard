"""SQLAlchemy adapter for the garden-trend read seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the window
itself is bound in SQL, but period bucketing is left to
`src/blizzard/hub/domain/garden/findings/trend.py`'s `compute_trend` (``bzh:sql-portable``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import aliased

from blizzard.hub.domain.garden.findings.trend import TREND_FACT_KINDS, IReadGardenTrendRepository, TrendFact
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import finding_facts, findings


class GardenTrendStore:
    """Read-only garden-trend adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def facts_for_trend(self, routine_name: str, *, since: datetime, until: datetime) -> list[TrendFact]:
        earlier = aliased(finding_facts)
        prior_kind = (
            select(earlier.c.kind)
            .where(earlier.c.finding_id == finding_facts.c.finding_id, earlier.c.id < finding_facts.c.id)
            .order_by(earlier.c.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        with self._store.read("facts_for_trend") as conn:
            rows = conn.execute(
                select(
                    finding_facts.c.kind,
                    finding_facts.c.recorded_at,
                    findings.c.introduced_at,
                    prior_kind.label("prior_kind"),
                )
                .select_from(finding_facts.join(findings, finding_facts.c.finding_id == findings.c.finding_id))
                .where(
                    findings.c.routine_name == routine_name,
                    finding_facts.c.kind.in_(TREND_FACT_KINDS),
                    finding_facts.c.recorded_at >= since,
                    finding_facts.c.recorded_at < until,
                )
            ).all()
        return [
            TrendFact(
                kind=row.kind,
                recorded_at=row.recorded_at,
                introduced_at=row.introduced_at,
                prior_kind=row.prior_kind if row.kind == "reopened" else None,
            )
            for row in rows
        ]


def _conforms_garden_trend_store(x: GardenTrendStore) -> IReadGardenTrendRepository:
    return x
