"""SQLAlchemy adapter for the routine repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Name uniqueness is enforced
by :class:`~blizzard.hub.domain.garden.routines.RoutineAuthoring`; ``uq_routines_name`` backstops a
concurrent create, its ``IntegrityError`` re-raised as ``RoutineNameTakenError``, never swallowed.
Every write commits its change row — and a default scope's mint — in the same transaction."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.roles import adapter_model
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.domain.garden.routines import IWriteRoutineRepository, Routine, RoutineNameTakenError
from blizzard.hub.domain.garden.scopes import ScopeMint
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.internal.scope_store import ensure_scope
from blizzard.hub.store.schema import routine_lifecycle_facts, routine_scopes, routines


@adapter_model
@dataclass(frozen=True)
class ModelColumn:
    """``routines.default_model``'s column shape — a JSON ``list[str]``, the
    ``chunks.default_model`` shape. An empty preference list writes
    ``NULL`` rather than ``"[]"``, so "express no preference" reads identically
    however the routine reached it."""

    def encode(self, preferences: list[str]) -> str | None:
        return json.dumps(list(preferences)) if preferences else None

    def decode(self, value: str | None) -> list[str]:
        return [str(m) for m in json.loads(value)] if value else []


MODEL = ModelColumn()
HARNESSES = ModelColumn()


def _columns(routine: Routine) -> dict[str, object]:
    return {
        "graph_name": routine.graph_name,
        "default_scope_slug": routine.default_scope_slug,
        "default_model": MODEL.encode(routine.default_model),
        "default_effort": routine.default_effort,
        "default_harnesses": HARNESSES.encode(routine.default_harnesses),
    }


def link_scope(conn: Connection, routine_id: str, scope_slug: str) -> None:
    """Link ``scope_slug`` into ``routine_id``'s set inside the caller's transaction; a
    no-op when already linked."""
    linked = conn.execute(
        select(routine_scopes.c.scope_slug).where(
            routine_scopes.c.routine_id == routine_id, routine_scopes.c.scope_slug == scope_slug
        )
    ).first()
    if linked is None:
        conn.execute(insert(routine_scopes).values(routine_id=routine_id, scope_slug=scope_slug))


def move_revision(conn: Connection, routine: Routine, from_revision: int, **values: object) -> None:
    """Compare-and-set the routine's revision from ``from_revision`` to ``routine.revision``,
    writing ``values`` beside it; :class:`ConfigRevisionConflict` when the stored revision has moved."""
    moved = conn.execute(
        update(routines)
        .where(routines.c.routine_id == routine.routine_id, routines.c.revision == from_revision)
        .values(revision=routine.revision, **values)
    ).rowcount
    if moved != 1:
        current = conn.execute(
            select(routines.c.revision).where(routines.c.routine_id == routine.routine_id)
        ).scalar_one()
        raise ConfigRevisionConflict("routine", routine.name, current=current)


def insert_routine(conn: Connection, routine: Routine, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction: the row, its default-scope link, and the change."""
    conn.execute(
        insert(routines).values(
            routine_id=routine.routine_id,
            name=routine.name,
            **_columns(routine),
            created_at=routine.created_at,
            revision=routine.revision,
        )
    )
    link_scope(conn, routine.routine_id, routine.default_scope_slug)
    append_change(conn, change)


def update_routine(conn: Connection, routine: Routine, from_revision: int, change: ConfigChange) -> None:
    """The edit body, inside the caller's transaction: the compare-and-set, the default-scope link, the change."""
    move_revision(conn, routine, from_revision, **_columns(routine))
    link_scope(conn, routine.routine_id, routine.default_scope_slug)
    append_change(conn, change)


def record_routine_lifecycle(
    conn: Connection,
    routine: Routine,
    *,
    retired: bool,
    from_revision: int,
    at: datetime,
    by: str,
    change: ConfigChange,
) -> None:
    """The lifecycle body, inside the caller's transaction: the compare-and-set, the fact, the change."""
    move_revision(conn, routine, from_revision)
    conn.execute(
        insert(routine_lifecycle_facts).values(routine_id=routine.routine_id, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


def set_linked_scopes(
    conn: Connection, routine: Routine, scopes: Sequence[str], *, from_revision: int, change: ConfigChange
) -> None:
    """Move ``routine``'s linked set to exactly ``scopes`` with the compare-and-set and the change."""
    move_revision(conn, routine, from_revision)
    conn.execute(
        delete(routine_scopes).where(
            routine_scopes.c.routine_id == routine.routine_id, routine_scopes.c.scope_slug.not_in(list(scopes))
        )
    )
    for slug in scopes:
        link_scope(conn, routine.routine_id, slug)
    append_change(conn, change)


class RoutineStore:
    """Read-write routine adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(self, routine: Routine, *, change: ConfigChange, scope_mint: ScopeMint | None) -> Routine:
        try:
            with self._store.write("create", expect=(IntegrityError,)) as conn:
                if scope_mint is not None:
                    ensure_scope(conn, scope_mint)
                insert_routine(conn, routine, change)
        except IntegrityError as exc:
            # The post-write re-check: a concurrent create that took the name first
            # loses here, and reads as the same refusal the domain's pre-check raises.
            if self.get_by_name(routine.name) is not None:
                raise RoutineNameTakenError(routine.name) from exc
            raise
        return routine

    def update(
        self, routine: Routine, *, from_revision: int, change: ConfigChange, scope_mint: ScopeMint | None
    ) -> Routine:
        with self._store.write("update", expect=(ConfigRevisionConflict,)) as conn:
            if scope_mint is not None:
                ensure_scope(conn, scope_mint)
            update_routine(conn, routine, from_revision, change)
        return routine

    def record_lifecycle(
        self, routine: Routine, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Routine:
        """Append a ``routine.retired``/``routine.enabled`` fact — newest-fact-wins — with
        the revision move and the change row."""
        with self._store.write("record_lifecycle", expect=(ConfigRevisionConflict,)) as conn:
            record_routine_lifecycle(
                conn, routine, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return routine

    def get(self, routine_id: str) -> Routine | None:
        return self._one(routines.c.routine_id == routine_id, "get")

    def get_by_name(self, name: str) -> Routine | None:
        return self._one(routines.c.name == name, "get_by_name")

    def get_many_by_name(self, names: Sequence[str]) -> dict[str, Routine]:
        found: dict[str, Routine] = {}
        if not names:
            return found
        with self._store.read("get_many_by_name") as conn:
            for batch in id_batches(names):
                rows = conn.execute(select(routines).where(routines.c.name.in_(batch))).all()
                retired = _retired_ids(conn, [row.routine_id for row in rows])
                found.update({row.name: self._of(row, retired=row.routine_id in retired) for row in rows})
        return found

    def _one(self, where, operation: str) -> Routine | None:  # type: ignore[no-untyped-def]
        with self._store.read(operation) as conn:
            row = conn.execute(select(routines).where(where)).one_or_none()
            if row is None:
                return None
            return self._of(row, retired=_is_retired(conn, row.routine_id))

    def list_all(self) -> list[Routine]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(routines).order_by(routines.c.created_at.desc())).all()
            retired = _retired_ids(conn)
        return [self._of(row, retired=row.routine_id in retired) for row in rows]

    def is_retired(self, routine_id: str) -> bool:
        with self._store.read("is_retired") as conn:
            return _is_retired(conn, routine_id)

    def retired_ids(self) -> set[str]:
        """Every routine id whose newest lifecycle fact reads retired — mirrors
        ``ScopeStore.retired_slugs``."""
        with self._store.read("retired_ids") as conn:
            return _retired_ids(conn)

    @staticmethod
    def _of(row, *, retired: bool) -> Routine:  # type: ignore[no-untyped-def]
        return Routine(
            routine_id=row.routine_id,
            name=row.name,
            graph_name=row.graph_name,
            default_scope_slug=row.default_scope_slug,
            created_at=row.created_at,
            default_model=MODEL.decode(row.default_model),
            default_effort=row.default_effort,
            default_harnesses=HARNESSES.decode(row.default_harnesses),
            revision=row.revision,
            retired=retired,
        )


def _is_retired(conn: Connection, routine_id: str) -> bool:
    """Newest ``routine_lifecycle_facts`` row for ``routine_id`` wins; no row reads
    not-retired (a freshly minted routine starts enabled)."""
    row = conn.execute(
        select(routine_lifecycle_facts.c.retired)
        .where(routine_lifecycle_facts.c.routine_id == routine_id)
        .order_by(routine_lifecycle_facts.c.id.desc())
        .limit(1)
    ).first()
    return bool(row.retired) if row is not None else False


def _retired_ids(conn: Connection, routine_ids: Sequence[str] | None = None) -> set[str]:
    found: set[str] = set()
    for batch in id_batches(routine_ids) if routine_ids is not None else [None]:
        found.update(
            conn.execute(
                newest_retired_select(routine_lifecycle_facts, routine_lifecycle_facts.c.routine_id, batch)
            ).scalars()
        )
    return found


def _conforms_routine_store(x: RoutineStore) -> IWriteRoutineRepository:
    return x
