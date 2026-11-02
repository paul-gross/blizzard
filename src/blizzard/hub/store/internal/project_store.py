"""SQLAlchemy adapter for the project seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). A slug move is one transaction: the
compare-and-set on the revision, the removal of any former-slug row holding the new slug, the left slug's former-slug
row, and the change row. ``uq_projects_tenant_slug`` refuses a slug another project holds."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.project_colors import HexColor, ProjectColor
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.projects.model import (
    IWriteProjectRepository,
    Project,
    ProjectId,
    ProjectSlug,
    ProjectSlugTaken,
)
from blizzard.hub.domain.projects.resolve import resolve
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.schema import project_former_slugs, projects
from blizzard.hub.store.scoped import ScopedConnections, statements_of

_SLUGS = SlugTables(projects, project_former_slugs, "project_id", ProjectSlugTaken)


def insert_project(conn: Connection, record: Project, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`ProjectSlugTaken` when refused."""
    _SLUGS.claim(conn, record.project_id, record.slug)
    conn.execute(
        statements_of(conn)
        .insert(projects)
        .values(
            project_id=record.project_id,
            slug=record.slug,
            name=record.name,
            description=record.description,
            color=record.color,
            revision=record.revision,
            created_at=record.created_at,
        )
    )
    append_change(conn, change)


def update_project(
    conn: Connection, record: Project, from_revision: int, left_slug: str | None, change: ConfigChange
) -> None:
    """The compare-and-set edit body, inside the caller's transaction, moving the slug when ``left_slug`` is set."""
    if left_slug is not None:
        _SLUGS.claim(conn, record.project_id, record.slug)
    moved = conn.execute(
        statements_of(conn)
        .update(projects)
        .where(projects.c.project_id == record.project_id, projects.c.revision == from_revision)
        .values(
            slug=record.slug,
            name=record.name,
            description=record.description,
            color=record.color,
            revision=record.revision,
        )
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("project", record.project_id, current=_revision(conn, record.project_id))
    if left_slug is not None:
        _SLUGS.release(conn, record.project_id, left_slug, left_at=change.recorded_at)
    append_change(conn, change)


def _revision(conn: Connection, project_id: str) -> int:
    return conn.execute(
        statements_of(conn).select(projects, projects.c.revision).where(projects.c.project_id == project_id)
    ).scalar_one()


class ProjectStore:
    """Read-write project adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(self, record: Project, *, change: ConfigChange) -> Project:
        try:
            with self._store.write("create", expect=(ProjectSlugTaken, IntegrityError)) as conn:
                insert_project(conn, record, change)
        except IntegrityError as exc:  # a concurrent write took the slug first
            raise ProjectSlugTaken(record.slug) from exc
        return record

    def update(self, record: Project, *, from_revision: int, left_slug: str | None, change: ConfigChange) -> Project:
        try:
            with self._store.write("update", expect=(ProjectSlugTaken, ConfigRevisionConflict, IntegrityError)) as conn:
                update_project(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:  # a concurrent write took the slug first
            raise ProjectSlugTaken(record.slug) from exc
        return record

    def get(self, project_id: ProjectId) -> Project | None:
        return self.get_many([project_id]).get(project_id)

    def get_many(self, project_ids: list[ProjectId]) -> dict[ProjectId, Project]:
        if not project_ids:
            return {}
        found: dict[ProjectId, Project] = {}
        with self._store.read("get_many") as conn:
            for batch in id_batches(project_ids):
                rows = conn.execute(self._store.select(projects).where(projects.c.project_id.in_(batch))).all()
                found.update({ProjectId(row.project_id): _of(row) for row in rows})
        return found

    def list_all(self) -> list[Project]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(projects).order_by(projects.c.slug)).all()
        return [_of(row) for row in rows]

    def get_by_slug(self, slug: ProjectSlug) -> Project | None:
        with self._store.read("get_by_slug") as conn:
            row = conn.execute(self._store.select(projects).where(projects.c.slug == slug.value)).first()
        return None if row is None else _of(row)

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None:
        left = self._store.scope_join(
            project_former_slugs, projects, project_former_slugs.c.project_id == projects.c.project_id
        )
        with self._store.read("get_by_former_slug") as conn:
            row = conn.execute(
                self._store.select(left, *projects.c).where(project_former_slugs.c.slug == slug.value)
            ).first()
        return None if row is None else _of(row)

    def resolve(self, value: str) -> Project | None:
        return resolve(value, self)


def _color_of(stored: str) -> ProjectColor | HexColor:
    """The palette color ``stored`` names, else the custom ``#rrggbb`` the hub stored."""
    try:
        return ProjectColor(stored)
    except ValueError:
        return stored


def _of(row: Row[Any]) -> Project:
    return Project(
        project_id=ProjectId(row.project_id),
        slug=row.slug,
        name=row.name,
        description=row.description,
        color=_color_of(row.color),
        revision=row.revision,
        created_at=row.created_at,
    )


_project_store_conforms: type[IWriteProjectRepository] = ProjectStore
