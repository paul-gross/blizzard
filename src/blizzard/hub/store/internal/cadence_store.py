"""SQLAlchemy adapter for the cadence repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Slug uniqueness is checked by
:class:`~blizzard.hub.domain.cadence.services.CadenceAuthoring`; ``uq_cadences_project_slug`` backstops a concurrent
create, its ``IntegrityError`` re-raised as ``CadenceSlugTakenError``, never swallowed. A chore's routine and scopes
are stored by slug, and ``cadence_chore_scopes`` holds rows only for a ``listed`` chore. Retired derives from the
append-only ``cadence_lifecycle_facts``, newest fact wins. Every write commits its change row in the same
transaction (``bzh:configured-record``); every read reconstitutes its cadences in bulk."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.cadence_anchor import CadenceAnchor
from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.cadence.model import (
    Cadence,
    CadenceSlugTakenError,
    Chore,
    ChoreSpec,
    Rhythm,
    ScopeSelection,
    Spacing,
)
from blizzard.hub.domain.cadence.ports import IWriteCadenceRepository
from blizzard.hub.domain.cadence.schedule import Schedule
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict

# Appends the config audit record on the caller's connection, atomically with the write.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import cadence_chore_scopes, cadence_chores, cadence_lifecycle_facts, cadences
from blizzard.hub.store.scoped import ScopedConnections, statements_of


def _columns(cadence: Cadence) -> dict[str, object]:
    rhythm = cadence.rhythm
    return {
        "name": cadence.name,
        "schedule_cron": None if rhythm.schedule is None else rhythm.schedule.cron,
        "schedule_zone": None if rhythm.schedule is None else rhythm.schedule.zone,
        "spacing_minutes": None if rhythm.spacing is None else rhythm.spacing.minutes,
        "anchor": None if rhythm.anchor is None else rhythm.anchor.value,
    }


def _chore_columns(chore: Chore, position: int) -> dict[str, object]:
    spec = chore.spec
    return {
        "routine_slug": spec.routine_slug,
        "scope_selection": spec.selection.value,
        "scopes_set_at": chore.scopes_set_at,
        "mode": spec.mode.value,
        "note": spec.note,
        "promote": spec.promote,
        "position": position,
    }


def _write_scopes(conn: Connection, chore: Chore) -> None:
    stmts = statements_of(conn)
    conn.execute(stmts.delete(cadence_chore_scopes).where(cadence_chore_scopes.c.chore_id == chore.chore_id))
    if chore.spec.selection is ScopeSelection.LISTED:
        for slug in chore.spec.scopes:
            conn.execute(stmts.insert(cadence_chore_scopes).values(chore_id=chore.chore_id, scope_slug=slug))


def _write_chores(conn: Connection, cadence: Cadence, *, at: datetime) -> None:
    """Make ``cadence``'s stored chores exactly its own, by id: a dropped chore's rows go, a kept one's row is
    rewritten in place, and a new one is inserted, created at ``at``."""
    stmts = statements_of(conn)
    held = set(
        conn.execute(
            stmts.select(cadence_chores, cadence_chores.c.chore_id).where(
                cadence_chores.c.cadence_id == cadence.cadence_id
            )
        ).scalars()
    )
    dropped = sorted(held - {chore.chore_id for chore in cadence.chores})
    for batch in id_batches(dropped):
        conn.execute(stmts.delete(cadence_chore_scopes).where(cadence_chore_scopes.c.chore_id.in_(batch)))
        conn.execute(stmts.delete(cadence_chores).where(cadence_chores.c.chore_id.in_(batch)))
    for position, chore in enumerate(cadence.chores):
        if chore.chore_id in held:
            conn.execute(
                stmts.update(cadence_chores)
                .where(cadence_chores.c.chore_id == chore.chore_id)
                .values(**_chore_columns(chore, position))
            )
        else:
            conn.execute(
                stmts.insert(cadence_chores).values(
                    chore_id=chore.chore_id,
                    cadence_id=cadence.cadence_id,
                    key=chore.key,
                    created_at=at,
                    **_chore_columns(chore, position),
                )
            )
        _write_scopes(conn, chore)


def _move_revision(conn: Connection, cadence: Cadence, from_revision: int, **values: object) -> None:
    """Compare-and-set the revision from ``from_revision`` to ``cadence.revision``, writing ``values`` beside it;
    :class:`ConfigRevisionConflict` when the stored revision has moved."""
    stmts = statements_of(conn)
    moved = conn.execute(
        stmts.update(cadences)
        .where(cadences.c.cadence_id == cadence.cadence_id, cadences.c.revision == from_revision)
        .values(revision=cadence.revision, **values)
    ).rowcount
    if moved != 1:
        current = conn.execute(
            stmts.select(cadences, cadences.c.revision).where(cadences.c.cadence_id == cadence.cadence_id)
        ).scalar_one()
        raise ConfigRevisionConflict("cadence", cadence.slug, current=current)


def insert_cadence(conn: Connection, cadence: Cadence, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction: the row, its chores, and the change."""
    conn.execute(
        statements_of(conn)
        .insert(cadences)
        .values(
            cadence_id=cadence.cadence_id,
            project_id=cadence.project_id,
            slug=cadence.slug,
            created_at=cadence.created_at,
            revision=cadence.revision,
            **_columns(cadence),
        )
    )
    _write_chores(conn, cadence, at=cadence.created_at)
    append_change(conn, change)


def update_cadence(conn: Connection, cadence: Cadence, from_revision: int, change: ConfigChange) -> None:
    """The edit body, inside the caller's transaction: the compare-and-set, the chores, and the change."""
    _move_revision(conn, cadence, from_revision, **_columns(cadence))
    _write_chores(conn, cadence, at=change.recorded_at)
    append_change(conn, change)


def record_cadence_lifecycle(
    conn: Connection,
    cadence: Cadence,
    *,
    retired: bool,
    from_revision: int,
    at: datetime,
    by: str,
    change: ConfigChange,
) -> None:
    """The lifecycle body, inside the caller's transaction: the compare-and-set, the fact, and the change."""
    _move_revision(conn, cadence, from_revision)
    conn.execute(
        statements_of(conn)
        .insert(cadence_lifecycle_facts)
        .values(cadence_id=cadence.cadence_id, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


class CadenceStore:
    """Read-write cadence adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(self, cadence: Cadence, *, change: ConfigChange) -> Cadence:
        try:
            with self._store.write("create", expect=(IntegrityError,)) as conn:
                insert_cadence(conn, cadence, change)
        except IntegrityError as exc:
            # A concurrent create that took the slug first loses here, and reads as the domain's own refusal.
            if self.get_by_slug(cadence.project_id, cadence.slug) is not None:
                raise CadenceSlugTakenError(cadence.slug) from exc
            raise
        return cadence

    def update(self, cadence: Cadence, *, from_revision: int, change: ConfigChange) -> Cadence:
        with self._store.write("update", expect=(ConfigRevisionConflict,)) as conn:
            update_cadence(conn, cadence, from_revision, change)
        return cadence

    def record_lifecycle(
        self, cadence: Cadence, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Cadence:
        with self._store.write("record_lifecycle", expect=(ConfigRevisionConflict,)) as conn:
            record_cadence_lifecycle(
                conn, cadence, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return cadence

    def get(self, cadence_id: str) -> Cadence | None:
        return self._one(cadences.c.cadence_id == cadence_id, "get")

    def get_by_slug(self, project_id: str, slug: str) -> Cadence | None:
        return self._one((cadences.c.project_id == project_id) & (cadences.c.slug == slug), "get_by_slug")

    def list_all(self, project_id: str | None = None) -> list[Cadence]:
        stmt = self._store.select(cadences)
        if project_id is not None:
            stmt = stmt.where(cadences.c.project_id == project_id)
        with self._store.read("list_all") as conn:
            rows = conn.execute(stmt.order_by(cadences.c.created_at.desc(), cadences.c.cadence_id.desc())).all()
            return _reconstituted(conn, rows)

    def _one(self, where: ColumnElement[bool], operation: str) -> Cadence | None:
        with self._store.read(operation) as conn:
            row = conn.execute(self._store.select(cadences).where(where)).one_or_none()
            if row is None:
                return None
            return _reconstituted(conn, [row])[0]


def _reconstituted(conn: Connection, rows: Sequence[Row[Any]]) -> list[Cadence]:
    """Each cadence row as a whole cadence, read with a bounded number of queries whatever the rows' number."""
    if not rows:
        return []
    stmts = statements_of(conn)
    ids = [row.cadence_id for row in rows]
    chore_rows: dict[str, list[Row[Any]]] = defaultdict(list)
    listed: dict[str, list[str]] = defaultdict(list)
    retired: set[str] = set()
    for batch in id_batches(ids):
        for chore in conn.execute(
            stmts.select(cadence_chores)
            .where(cadence_chores.c.cadence_id.in_(batch))
            .order_by(cadence_chores.c.cadence_id, cadence_chores.c.position)
        ):
            chore_rows[chore.cadence_id].append(chore)
        retired.update(
            conn.execute(
                newest_retired_select(stmts, cadence_lifecycle_facts, cadence_lifecycle_facts.c.cadence_id, batch)
            ).scalars()
        )
    chore_ids = [c.chore_id for held in chore_rows.values() for c in held if c.scope_selection == "listed"]
    for batch in id_batches(chore_ids):
        for link in conn.execute(
            stmts.select(cadence_chore_scopes)
            .where(cadence_chore_scopes.c.chore_id.in_(batch))
            .order_by(cadence_chore_scopes.c.chore_id, cadence_chore_scopes.c.scope_slug)
        ):
            listed[link.chore_id].append(link.scope_slug)
    return [
        _cadence_of(
            row, [_chore_of(c, listed[c.chore_id]) for c in chore_rows[row.cadence_id]], row.cadence_id in retired
        )
        for row in rows
    ]


def _chore_of(row: Row[Any], scopes: list[str]) -> Chore:
    spec = ChoreSpec(
        key=row.key,
        routine_slug=row.routine_slug,
        selection=ScopeSelection(row.scope_selection),
        scopes=tuple(scopes),
        mode=RunMode(row.mode),
        note=row.note,
        promote=bool(row.promote),
    )
    return Chore(chore_id=row.chore_id, spec=spec, scopes_set_at=row.scopes_set_at)


def _cadence_of(row: Row[Any], chores: list[Chore], retired: bool) -> Cadence:
    schedule = None if row.schedule_cron is None else Schedule(row.schedule_cron, row.schedule_zone)
    spacing = None if row.spacing_minutes is None else Spacing(row.spacing_minutes)
    anchor = None if row.anchor is None else CadenceAnchor(row.anchor)
    return Cadence(
        cadence_id=row.cadence_id,
        slug=row.slug,
        name=row.name,
        rhythm=Rhythm(schedule, spacing, anchor),
        chores=tuple(chores),
        created_at=row.created_at,
        project_id=row.project_id,
        revision=row.revision,
        retired=retired,
    )


_cadence_store_conforms: type[IWriteCadenceRepository] = CadenceStore
