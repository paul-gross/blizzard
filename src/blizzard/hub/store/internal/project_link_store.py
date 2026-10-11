"""SQLAlchemy adapter for the project link seams (package-private).

Whether a link stands derives from its newest fact in ``project_source_link_facts`` /
``project_repository_link_facts`` (``bzh:facts-not-status``). Every write appends a fact and its change row in the
transaction that writes the link. Source links and repository links share every statement; :class:`_Kind` names the
tables and the name column one kind uses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Table, func
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict, is_built_in
from blizzard.hub.domain.projects.links import IWriteProjectLinks, RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.schema import (
    project_repository_link_facts,
    project_repository_links,
    project_source_link_facts,
    project_source_links,
)
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of


@dataclass(frozen=True)
class _Kind:
    """One link kind: its link and fact tables, the column naming the linked record, and its conflict label."""

    links: Table
    facts: Table
    column: str
    label: str

    def name(self, table: Table) -> Column[Any]:
        return table.c[self.column]


_SOURCES = _Kind(project_source_links, project_source_link_facts, "source_name", "source link")
_REPOSITORIES = _Kind(project_repository_links, project_repository_link_facts, "repository_name", "repository link")

#: The ``keyed_locks`` namespace of one project's source links, keyed by the project's id. Every source link or
#: unlink takes its project's key, and an ingest takes the key of the project it mints into before its pointers'
#: keys, so the links an ingest read stand until it commits.
PROJECT_SOURCE_LINKS_LOCK_NAMESPACE = "project-source-links"


def _lock_source_links(conn: Connection, kind: _Kind, project_id: str) -> None:
    if kind is _SOURCES:
        lock_keys(conn, PROJECT_SOURCE_LINKS_LOCK_NAMESPACE, [project_id])


def _append_fact(conn: Connection, kind: _Kind, project_id: str, name: str, linked: bool, change: ConfigChange) -> None:
    conn.execute(
        statements_of(conn)
        .insert(kind.facts)
        .values(
            {
                "project_id": project_id,
                kind.column: name,
                "linked": linked,
                "set_at": change.recorded_at,
                "set_by": change.actor,
            }
        )
    )


def insert_link(conn: Connection, kind: _Kind, project_id: str, name: str, change: ConfigChange) -> None:
    """The first-link body, inside the caller's transaction: the link at revision 1, its linked fact, the change —
    a source link under its project's lock."""
    _lock_source_links(conn, kind, project_id)
    conn.execute(
        statements_of(conn).insert(kind.links).values({"project_id": project_id, kind.column: name, "revision": 1})
    )
    _append_fact(conn, kind, project_id, name, True, change)
    append_change(conn, change)


def record_link(
    conn: Connection,
    kind: _Kind,
    project_id: str,
    name: str,
    *,
    linked: bool,
    revision: int,
    from_revision: int,
    change: ConfigChange,
) -> None:
    """The unlink/relink body, inside the caller's transaction: compare-and-set the revision, append the fact and
    the change — a source link under its project's lock."""
    _lock_source_links(conn, kind, project_id)
    links = kind.links
    moved = conn.execute(
        statements_of(conn)
        .update(links)
        .where(links.c.project_id == project_id, kind.name(links) == name, links.c.revision == from_revision)
        .values(revision=revision)
    ).rowcount
    if moved != 1:
        current = conn.execute(
            statements_of(conn)
            .select(links, links.c.revision)
            .where(links.c.project_id == project_id, kind.name(links) == name)
        ).scalar_one()
        raise ConfigRevisionConflict(kind.label, f"{project_id}/{name}", current=current)
    _append_fact(conn, kind, project_id, name, linked, change)
    append_change(conn, change)


def _newest_linked(
    stmts: ScopedStatements, conn: Connection, kind: _Kind, project_id: str, name: str, *, at: datetime | None = None
) -> bool:
    """Whether the newest fact of one link — set by ``at``, when given — reads linked; ``False`` with none."""
    facts = kind.facts
    query = (
        stmts.select(facts, facts.c.linked)
        .where(facts.c.project_id == project_id, kind.name(facts) == name)
        .order_by(facts.c.id.desc())
        .limit(1)
    )
    if at is not None:
        query = query.where(facts.c.set_at <= at)
    return bool(conn.execute(query).scalar())


def _standing(
    stmts: ScopedStatements, conn: Connection, kind: _Kind, project_ids: Sequence[str], name: str | None = None
) -> set[tuple[str, str]]:
    """The ``(project_id, name)`` links of ``project_ids`` — narrowed to ``name`` when given — whose newest fact
    reads linked: one group-by-max join per batch (``bzh:newest-per-key-read``)."""
    facts = kind.facts
    standing: set[tuple[str, str]] = set()
    for batch in id_batches(list(project_ids)):
        group = stmts.select(facts, facts.c.project_id, kind.name(facts), func.max(facts.c.id).label("id")).where(
            facts.c.project_id.in_(batch)
        )
        if name is not None:
            group = group.where(kind.name(facts) == name)
        newest = group.group_by(facts.c.project_id, kind.name(facts)).subquery()
        rows = conn.execute(
            stmts.select(facts, facts.c.project_id, kind.name(facts))
            .join(newest, facts.c.id == newest.c.id)
            .where(facts.c.linked.is_(True))
        ).all()
        standing.update((row[0], row[1]) for row in rows)
    return standing


def projects_linking_source_conn(conn: Connection, source_name: str) -> list[ProjectId]:
    """The projects whose standing link draws from ``source_name``, ordered by id, read on the caller's ``conn`` —
    the locked ingest's read, and :meth:`ProjectLinkStore.projects_linking_source`'s body."""
    stmts = statements_of(conn)
    links = _SOURCES.links
    linking = conn.execute(stmts.select(links, links.c.project_id).where(links.c.source_name == source_name)).scalars()
    standing = _standing(stmts, conn, _SOURCES, list(linking), source_name)
    return sorted(ProjectId(project_id) for project_id, _ in standing)


class ProjectLinkStore:
    """Read-write link adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    # --- writes ---

    def create_source_link(self, link: SourceLink, *, change: ConfigChange) -> SourceLink:
        self._create("create_source_link", _SOURCES, link.project_id, link.source_name, change)
        return link

    def record_source_link(self, link: SourceLink, *, from_revision: int, change: ConfigChange) -> SourceLink:
        self._record(
            "record_source_link",
            _SOURCES,
            link.project_id,
            link.source_name,
            link.linked,
            link.revision,
            from_revision,
            change,
        )
        return link

    def create_repository_link(self, link: RepositoryLink, *, change: ConfigChange) -> RepositoryLink:
        self._create("create_repository_link", _REPOSITORIES, link.project_id, link.repository_name, change)
        return link

    def record_repository_link(
        self, link: RepositoryLink, *, from_revision: int, change: ConfigChange
    ) -> RepositoryLink:
        self._record(
            "record_repository_link",
            _REPOSITORIES,
            link.project_id,
            link.repository_name,
            link.linked,
            link.revision,
            from_revision,
            change,
        )
        return link

    def _create(self, operation: str, kind: _Kind, project_id: str, name: str, change: ConfigChange) -> None:
        try:
            with self._store.write(operation, expect=(IntegrityError,)) as conn:
                insert_link(conn, kind, project_id, name, change)
        except IntegrityError as exc:  # a concurrent first link of the same pair landed first
            raise ConfigRevisionConflict(kind.label, f"{project_id}/{name}", current=1) from exc

    def _record(
        self,
        operation: str,
        kind: _Kind,
        project_id: str,
        name: str,
        linked: bool,
        revision: int,
        from_revision: int,
        change: ConfigChange,
    ) -> None:
        with self._store.write(operation, expect=(ConfigRevisionConflict,)) as conn:
            record_link(
                conn,
                kind,
                project_id,
                name,
                linked=linked,
                revision=revision,
                from_revision=from_revision,
                change=change,
            )

    # --- reads ---

    def source_link(self, project_id: ProjectId, source_name: str) -> SourceLink | None:
        found = self._stored("source_link", _SOURCES, project_id, source_name)
        if found is None:
            return None
        revision, linked = found
        return SourceLink(project_id=project_id, source_name=source_name, revision=revision, linked=linked)

    def repository_link(self, project_id: ProjectId, repository_name: str) -> RepositoryLink | None:
        found = self._stored("repository_link", _REPOSITORIES, project_id, repository_name)
        if found is None:
            return None
        revision, linked = found
        return RepositoryLink(project_id=project_id, repository_name=repository_name, revision=revision, linked=linked)

    def sources_of(self, project_id: ProjectId) -> list[SourceLink]:
        return [
            SourceLink(project_id=project_id, source_name=name, revision=revision)
            for name, revision in self._standing_of("sources_of", _SOURCES, project_id)
        ]

    def repositories_of(self, project_id: ProjectId) -> list[RepositoryLink]:
        return [
            RepositoryLink(project_id=project_id, repository_name=name, revision=revision)
            for name, revision in self._standing_of("repositories_of", _REPOSITORIES, project_id)
        ]

    def projects_linking_source(self, source_name: str) -> list[ProjectId]:
        with self._store.read("projects_linking_source") as conn:
            return projects_linking_source_conn(conn, source_name)

    def linked_at(self, project_id: ProjectId, source_name: str, at: datetime) -> bool:
        if is_built_in(source_name):
            return True
        with self._store.read("linked_at") as conn:
            return _newest_linked(self._store, conn, _SOURCES, project_id, source_name, at=at)

    def _stored(self, operation: str, kind: _Kind, project_id: str, name: str) -> tuple[int, bool] | None:
        """The stored link's revision and whether it stands, or ``None`` when the project never linked ``name``."""
        links = kind.links
        with self._store.read(operation) as conn:
            revision = conn.execute(
                self._store.select(links, links.c.revision).where(
                    links.c.project_id == project_id, kind.name(links) == name
                )
            ).scalar()
            if revision is None:
                return None
            return revision, _newest_linked(self._store, conn, kind, project_id, name)

    def _standing_of(self, operation: str, kind: _Kind, project_id: str) -> list[tuple[str, int]]:
        """The standing links of one project as ``(name, revision)``, ordered by name: the names its newest facts
        read linked, then those links' revisions by name."""
        links = kind.links
        found: dict[str, int] = {}
        with self._store.read(operation) as conn:
            names = sorted(name for _, name in _standing(self._store, conn, kind, [project_id]))
            for batch in id_batches(names):
                rows = conn.execute(
                    self._store.select(links, kind.name(links), links.c.revision).where(
                        links.c.project_id == project_id, kind.name(links).in_(batch)
                    )
                ).all()
                found.update({row[0]: row[1] for row in rows})
        return [(name, found[name]) for name in names if name in found]


def _conforms_link_store(x: ProjectLinkStore) -> IWriteProjectLinks:
    return x
