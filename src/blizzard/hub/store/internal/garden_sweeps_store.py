"""SQLAlchemy adapter for the garden-sweeps read seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the newest set per
scope and the measurement window are both applied in SQL (``bzh:newest-per-key-read``)."""

from __future__ import annotations

import json
from datetime import datetime

from blizzard.hub.domain.garden.runs.sweeps import IReadGardenSweepsRepository, MeasurementReading, SweepFact
from blizzard.hub.store.internal.newest_fact import newer_row_exists
from blizzard.hub.store.schema import artifacts, finding_sets
from blizzard.hub.store.scoped import ScopedConnections


class GardenSweepsStore:
    """Read-only garden-sweeps adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def last_swept_by_scope(self, project_id: str, routine_name: str) -> list[SweepFact]:
        """Per scope, the set no newer set of the same routine beats in ``(produced_at, finding_set_id)``
        order — ``finding_sets`` carries no timestamp of its own, so each side joins its own artifact
        (``tests/test_garden_sweeps_component.py::test_the_newest_set_by_produced_at_is_reported``)."""
        rival_sets = finding_sets.alias("rival_sets")
        rival_artifacts = artifacts.alias("rival_artifacts")
        beaten = newer_row_exists(
            self._store,
            self._store.scope_join(
                rival_sets, rival_artifacts, rival_sets.c.artifact_id == rival_artifacts.c.artifact_id
            ),
            partition=[
                (finding_sets.c.project_id, rival_sets.c.project_id),
                (finding_sets.c.routine_name, rival_sets.c.routine_name),
                (finding_sets.c.scope_slug, rival_sets.c.scope_slug),
            ],
            order=[
                (artifacts.c.produced_at, rival_artifacts.c.produced_at),
                (finding_sets.c.finding_set_id, rival_sets.c.finding_set_id),
            ],
        )
        with self._store.read("last_swept_by_scope") as conn:
            rows = conn.execute(
                self._store.select(
                    self._store.scope_join(
                        finding_sets, artifacts, finding_sets.c.artifact_id == artifacts.c.artifact_id
                    ),
                    finding_sets.c.finding_set_id,
                    finding_sets.c.scope_slug,
                    finding_sets.c.revisions,
                    artifacts.c.produced_at,
                ).where(finding_sets.c.project_id == project_id, finding_sets.c.routine_name == routine_name, ~beaten)
            ).all()
        return [
            SweepFact(
                finding_set_id=row.finding_set_id,
                scope_slug=row.scope_slug,
                produced_at=row.produced_at,
                revisions=json.loads(row.revisions),
            )
            for row in rows
        ]

    def measurements_in_window(
        self, project_id: str, routine_name: str, *, since: datetime, until: datetime
    ) -> list[MeasurementReading]:
        with self._store.read("measurements_in_window") as conn:
            rows = conn.execute(
                self._store.select(
                    self._store.scope_join(
                        finding_sets, artifacts, finding_sets.c.artifact_id == artifacts.c.artifact_id
                    ),
                    finding_sets.c.scope_slug,
                    finding_sets.c.measurement,
                    artifacts.c.produced_at,
                )
                .where(
                    finding_sets.c.project_id == project_id,
                    finding_sets.c.routine_name == routine_name,
                    finding_sets.c.measurement.is_not(None),
                    artifacts.c.produced_at >= since,
                    artifacts.c.produced_at < until,
                )
                # finding_set_id is the always-distinct second term (`bzh:sql-portable`).
                .order_by(artifacts.c.produced_at, finding_sets.c.finding_set_id)
            ).all()
        return [
            MeasurementReading(scope_slug=row.scope_slug, produced_at=row.produced_at, measurement=row.measurement)
            for row in rows
        ]


_garden_sweeps_store_conforms: type[IReadGardenSweepsRepository] = GardenSweepsStore
