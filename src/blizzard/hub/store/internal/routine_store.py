"""SQLAlchemy adapter for the routine repository seam (package-private, issue #389).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Name uniqueness
is enforced by :class:`~blizzard.hub.domain.routines.RoutineAuthoring` before ``create``
runs; ``uq_routines_name`` is a backstop only — D9's one caught library exception is D5's
idempotent CAS, which this is not."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import insert, select, update

from blizzard.hub.domain.routines import IWriteRoutineRepository, Routine
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import routines


@dataclass(frozen=True)
class ModelColumn:
    """``routines.default_model``'s column shape — a JSON ``list[str]``, the
    ``chunks.default_model`` shape (issue #144). An empty preference list writes
    ``NULL`` rather than ``"[]"``, so "express no preference" reads identically
    however the routine reached it."""

    def encode(self, preferences: list[str]) -> str | None:
        return json.dumps(list(preferences)) if preferences else None

    def decode(self, value: str | None) -> list[str]:
        return [str(m) for m in json.loads(value)] if value else []


MODEL = ModelColumn()


class RoutineStore:
    """Read-write routine adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(self, routine: Routine) -> None:
        with self._store.write("create") as conn:
            conn.execute(
                insert(routines).values(
                    routine_id=routine.routine_id,
                    name=routine.name,
                    graph_name=routine.graph_name,
                    default_scope_slug=routine.default_scope_slug,
                    default_model=MODEL.encode(routine.default_model),
                    default_effort=routine.default_effort,
                    created_at=routine.created_at,
                )
            )

    def edit(
        self,
        routine_id: str,
        *,
        graph_name: str,
        default_scope_slug: str,
        default_model: list[str],
        default_effort: str | None,
    ) -> Routine:
        with self._store.write("edit") as conn:
            conn.execute(
                update(routines)
                .where(routines.c.routine_id == routine_id)
                .values(
                    graph_name=graph_name,
                    default_scope_slug=default_scope_slug,
                    default_model=MODEL.encode(default_model),
                    default_effort=default_effort,
                )
            )
            row = conn.execute(select(routines).where(routines.c.routine_id == routine_id)).one()
        return self._of(row)

    def get(self, routine_id: str) -> Routine | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(routines).where(routines.c.routine_id == routine_id)).one_or_none()
        return self._of(row) if row is not None else None

    def get_by_name(self, name: str) -> Routine | None:
        with self._store.read("get_by_name") as conn:
            row = conn.execute(select(routines).where(routines.c.name == name)).one_or_none()
        return self._of(row) if row is not None else None

    def list_all(self) -> list[Routine]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(routines).order_by(routines.c.created_at.desc())).all()
        return [self._of(row) for row in rows]

    @staticmethod
    def _of(row) -> Routine:  # type: ignore[no-untyped-def]
        return Routine(
            routine_id=row.routine_id,
            name=row.name,
            graph_name=row.graph_name,
            default_scope_slug=row.default_scope_slug,
            created_at=row.created_at,
            default_model=MODEL.decode(row.default_model),
            default_effort=row.default_effort,
        )


def _conforms_routine_store(x: RoutineStore) -> IWriteRoutineRepository:
    return x
