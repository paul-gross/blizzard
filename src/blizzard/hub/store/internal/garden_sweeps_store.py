"""SQLAlchemy adapter for the garden-sweeps read seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the window
itself is applied in `src/blizzard/hub/domain/garden_sweeps.py`'s `compute_sweeps`
(D6, ``bzh:sql-portable``)."""

from __future__ import annotations

import json

from sqlalchemy import select

from blizzard.hub.domain.garden_sweeps import IReadGardenSweepsRepository, SweepFact
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import artifacts, finding_sets


class GardenSweepsStore:
    """Read-only garden-sweeps adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def sweeps_for_routine(self, routine_name: str) -> list[SweepFact]:
        with self._store.read("sweeps_for_routine") as conn:
            rows = conn.execute(
                select(
                    finding_sets.c.finding_set_id,
                    finding_sets.c.scope_slug,
                    finding_sets.c.revisions,
                    finding_sets.c.measurement,
                    artifacts.c.produced_at,
                )
                .select_from(finding_sets.join(artifacts, finding_sets.c.artifact_id == artifacts.c.artifact_id))
                .where(finding_sets.c.routine_name == routine_name)
            ).all()
        return [
            SweepFact(
                finding_set_id=row.finding_set_id,
                scope_slug=row.scope_slug,
                produced_at=row.produced_at,
                revisions=json.loads(row.revisions),
                measurement=row.measurement,
            )
            for row in rows
        ]


def _conforms_garden_sweeps_store(x: GardenSweepsStore) -> IReadGardenSweepsRepository:
    return x
