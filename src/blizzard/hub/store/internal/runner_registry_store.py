"""SQLAlchemy adapter for the fleet-registry seam (package-private).

Implements :class:`~blizzard.hub.domain.registry.IWriteRunnerRegistry` over the hub's
``runner_registrations``, ``runner_pause_facts``, and ``runner_local_pause_facts`` tables.
All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain sees
only :class:`~blizzard.hub.domain.registry.RunnerRegistration`.

Facts only, status derived (``bzh:facts-not-status``): neither brake is a column on the
registration. ``hub_paused`` derives from the newest ``runner_pause_facts`` row
and ``locally_paused`` from the newest ``runner_local_pause_facts`` row
— two independent fact streams with two different authors (issue #43): the fleet sets the
first here, the runner reports the second up through its outbound buffer.

``last_seen_at`` is the one refreshed timestamp (not a fact), bumped by registration and
the heartbeat; liveness derives from it in the domain, against the clock. Timestamps
arrive already stamped from the injected clock (``bzh:injected-clock``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, insert, select

from blizzard.hub.domain.registry import IWriteRunnerRegistry, RunnerRegistration
from blizzard.hub.store import schema as s


class RunnerRegistryStore:
    """Read-write fleet-registry adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads --------------------------------------------------------------

    def get_runner(self, runner_id: str) -> RunnerRegistration | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.runner_registrations).where(s.runner_registrations.c.runner_id == runner_id)
            ).one_or_none()
            if row is None:
                return None
            return self._registration(row, self._paused(conn, runner_id), self._locally_paused(conn, runner_id))

    def list_runners(self) -> list[RunnerRegistration]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.runner_registrations).order_by(s.runner_registrations.c.registered_at)).all()
            return [
                self._registration(row, self._paused(conn, row.runner_id), self._locally_paused(conn, row.runner_id))
                for row in rows
            ]

    # --- writes -------------------------------------------------------------

    def upsert_registration(self, runner_id: str, *, workspace_id: str, at: datetime) -> bool:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(s.runner_registrations.c.runner_id).where(s.runner_registrations.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(
                    insert(s.runner_registrations).values(
                        runner_id=runner_id, workspace_id=workspace_id, registered_at=at, last_seen_at=at
                    )
                )
                return True
            conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(workspace_id=workspace_id, last_seen_at=at)
            )
            return False

    def touch_last_seen(self, runner_id: str, *, at: datetime) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                s.runner_registrations.update()
                .where(s.runner_registrations.c.runner_id == runner_id)
                .values(last_seen_at=at)
            )
            return bool(result.rowcount)

    def record_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(s.runner_pause_facts).values(runner_id=runner_id, paused=paused, set_at=at, set_by=by))

    def record_local_pause(self, runner_id: str, *, paused: bool, at: datetime, by: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.runner_local_pause_facts).values(runner_id=runner_id, paused=paused, set_at=at, set_by=by)
            )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _paused(conn, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Derive the fleet's brake from the newest pause/resume fact, default False."""
        return RunnerRegistryStore._newest(conn, s.runner_pause_facts, runner_id)

    @staticmethod
    def _locally_paused(conn, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Derive the runner's own brake from the newest fact it reported (issue #43).

        Defaults False: a runner that has never reported one is not locally paused — and a
        runner the hub has never heard from at all is simply not claiming anyway."""
        return RunnerRegistryStore._newest(conn, s.runner_local_pause_facts, runner_id)

    @staticmethod
    def _newest(conn, table, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        row = conn.execute(
            select(table.c.paused).where(table.c.runner_id == runner_id).order_by(table.c.id.desc()).limit(1)
        ).one_or_none()
        return bool(row.paused) if row is not None else False

    @staticmethod
    def _registration(row, hub_paused: bool, locally_paused: bool) -> RunnerRegistration:  # type: ignore[no-untyped-def]
        return RunnerRegistration(
            runner_id=row.runner_id,
            workspace_id=row.workspace_id,
            registered_at=row.registered_at,
            last_seen_at=row.last_seen_at,
            hub_paused=hub_paused,
            locally_paused=locally_paused,
        )


def _conforms_registry(x: RunnerRegistryStore) -> IWriteRunnerRegistry:
    return x
