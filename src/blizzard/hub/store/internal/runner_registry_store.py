"""SQLAlchemy adapter for the fleet-registry seam (package-private).

Implements :class:`~blizzard.hub.domain.registry.IWriteRunnerRegistry` over the hub's
``runner_registrations`` and ``runner_pause_facts`` tables. All ``sqlalchemy`` usage is
confined here (``bzh:dependency-inversion``); the domain sees only
:class:`~blizzard.hub.domain.registry.RunnerRegistration`.

Facts only, status derived (``bzh:facts-not-status``): ``paused`` is never a column on
the registration — it is derived from the newest ``runner_pause_facts`` row (D-043, the
D-039 pattern). ``last_seen_at`` is the one refreshed timestamp (not a fact), bumped by
registration and the heartbeat; liveness derives from it in the domain, against the
clock. Timestamps arrive already stamped from the injected clock (``bzh:injected-clock``).
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
            return self._registration(row, self._paused(conn, runner_id))

    def list_runners(self) -> list[RunnerRegistration]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.runner_registrations).order_by(s.runner_registrations.c.registered_at)).all()
            return [self._registration(row, self._paused(conn, row.runner_id)) for row in rows]

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

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _paused(conn, runner_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Derive paused from the newest pause/resume fact (D-043), default False."""
        row = conn.execute(
            select(s.runner_pause_facts.c.paused)
            .where(s.runner_pause_facts.c.runner_id == runner_id)
            .order_by(s.runner_pause_facts.c.id.desc())
            .limit(1)
        ).one_or_none()
        return bool(row.paused) if row is not None else False

    @staticmethod
    def _registration(row, paused: bool) -> RunnerRegistration:  # type: ignore[no-untyped-def]
        return RunnerRegistration(
            runner_id=row.runner_id,
            workspace_id=row.workspace_id,
            registered_at=row.registered_at,
            last_seen_at=row.last_seen_at,
            paused=paused,
        )


def _conforms_registry(x: RunnerRegistryStore) -> IWriteRunnerRegistry:
    return x
