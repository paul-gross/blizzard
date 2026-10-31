"""SQLAlchemy adapter for the project link seams (package-private).

Whether a link stands derives from its newest fact in ``project_source_link_facts`` /
``project_repository_link_facts`` (``bzh:facts-not-status``). Every write appends a fact and its change row in the
transaction that writes the link. Source links and repository links share every statement; :class:`LinkKind` names the
tables and the id column one kind uses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Table, func
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceId, is_built_in
from blizzard.hub.domain.projects.links import IWriteProjectLinks, RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.project_position_store import newest_positions
from blizzard.hub.store.schema import (
    project_repository_link_facts,
    project_repository_links,
    project_source_link_facts,
    project_source_links,
)
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of


@dataclass(frozen=True)
class LinkKind:
    """One link kind: its link and fact tables, the column naming the linked record, and its conflict label."""

    links: Table
    facts: Table
    column: str
    label: str

    def name(self, table: Table) -> Column[Any]:
        return table.c[self.column]


_SOURCES = LinkKind(project_source_links, project_source_link_facts, "work_source_id", "source link")
_REPOSITORIES = LinkKind(project_repository_links, project_repository_link_facts, "repository_id", "repository link")

#: The link tables each link record kind writes to.
LINK_KINDS: Mapping[RecordKind, LinkKind] = {
    RecordKind.PROJECT_SOURCE_LINK: _SOURCES,
    RecordKind.PROJECT_REPOSITORY_LINK: _REPOSITORIES,
}

#: The ``keyed_locks`` namespace of one project's source links, keyed by the project's id. Every source link or
#: unlink takes its project's key.
PROJECT_SOURCE_LINKS_LOCK_NAMESPACE = "project-source-links"


def _lock_source_links(conn: Connection, kind: LinkKind, project_id: str) -> None:
    if kind is _SOURCES:
        lock_keys(conn, PROJECT_SOURCE_LINKS_LOCK_NAMESPACE, [project_id])


def _append_fact(
    conn: Connection, kind: LinkKind, project_id: str, name: str, linked: bool, change: ConfigChange
) -> None:
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


def insert_link(conn: Connection, kind: LinkKind, project_id: str, name: str, change: ConfigChange) -> None:
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
    kind: LinkKind,
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
    stmts: ScopedStatements, conn: Connection, kind: LinkKind, project_id: str, name: str, *, at: datetime | None = None
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


def _newest(
    stmts: ScopedStatements,
    conn: Connection,
    kind: LinkKind,
    project_ids: Sequence[str],
    names: Sequence[str] | None = None,
    *,
    standing_only: bool,
) -> dict[tuple[str, str], bool]:
    """Whether the newest fact of each ``(project_id, name)`` link of ``project_ids`` — narrowed to ``names`` when
    given — reads linked; with ``standing_only``, only the links that do. One group-by-max join per batch
    (``bzh:newest-per-key-read``)."""
    facts = kind.facts
    found: dict[tuple[str, str], bool] = {}
    for project_batch in id_batches(list(project_ids)):
        for name_batch in id_batches(list(names)) if names is not None else [None]:
            group = stmts.select(facts, facts.c.project_id, kind.name(facts), func.max(facts.c.id).label("id")).where(
                facts.c.project_id.in_(project_batch)
            )
            if name_batch is not None:
                group = group.where(kind.name(facts).in_(name_batch))
            newest = group.group_by(facts.c.project_id, kind.name(facts)).subquery()
            query = stmts.select(facts, facts.c.project_id, kind.name(facts), facts.c.linked).join(
                newest, facts.c.id == newest.c.id
            )
            if standing_only:
                query = query.where(facts.c.linked.is_(True))
            found.update(((row[0], row[1]), bool(row[2])) for row in conn.execute(query).all())
    return found


def _standing(
    stmts: ScopedStatements,
    conn: Connection,
    kind: LinkKind,
    project_ids: Sequence[str],
    names: Sequence[str] | None = None,
) -> set[tuple[str, str]]:
    """The ``(project_id, name)`` links of ``project_ids`` — narrowed to ``names`` when given — whose newest fact
    reads linked."""
    return set(_newest(stmts, conn, kind, project_ids, names, standing_only=True))


def _revisions(
    stmts: ScopedStatements, conn: Connection, kind: LinkKind, pairs: set[tuple[str, str]]
) -> dict[tuple[str, str], int]:
    """The stored revision of each link of ``pairs``; a pair never linked is dropped. Each batch reads the links of
    its projects by names, kept to the pairs asked for."""
    links = kind.links
    found: dict[tuple[str, str], int] = {}
    for project_batch in id_batches(sorted({project_id for project_id, _ in pairs})):
        for name_batch in id_batches(sorted({name for _, name in pairs})):
            rows = conn.execute(
                stmts.select(links, links.c.project_id, kind.name(links), links.c.revision).where(
                    links.c.project_id.in_(project_batch), kind.name(links).in_(name_batch)
                )
            ).all()
            found.update(((row[0], row[1]), row[2]) for row in rows if (row[0], row[1]) in pairs)
    return found


def projects_linking_sources_conn(conn: Connection, work_source_ids: Sequence[str]) -> dict[str, list[ProjectId]]:
    """Each of ``work_source_ids`` mapped to the projects whose standing link draws from it, ordered by id — a source
    no project links dropped — read on the caller's connection, inside the caller's transaction."""
    stmts = statements_of(conn)
    links = _SOURCES.links
    found: dict[str, list[ProjectId]] = {}
    for batch in id_batches(list(dict.fromkeys(work_source_ids))):
        linking = conn.execute(
            stmts.select(links, links.c.project_id).where(links.c.work_source_id.in_(batch)).distinct()
        ).scalars()
        for project_id, source in sorted(_standing(stmts, conn, _SOURCES, list(linking), batch)):
            found.setdefault(source, []).append(ProjectId(project_id))
    return found


class ProjectLinkStore:
    """Read-write link adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    # --- writes ---

    def create_source_link(self, link: SourceLink, *, change: ConfigChange) -> SourceLink:
        self._create("create_source_link", _SOURCES, link.project_id, link.work_source_id, change)
        return link

    def record_source_link(self, link: SourceLink, *, from_revision: int, change: ConfigChange) -> SourceLink:
        self._record(
            "record_source_link",
            _SOURCES,
            link.project_id,
            link.work_source_id,
            link.linked,
            link.revision,
            from_revision,
            change,
        )
        return link

    def create_repository_link(self, link: RepositoryLink, *, change: ConfigChange) -> RepositoryLink:
        self._create("create_repository_link", _REPOSITORIES, link.project_id, link.repository_id, change)
        return link

    def record_repository_link(
        self, link: RepositoryLink, *, from_revision: int, change: ConfigChange
    ) -> RepositoryLink:
        self._record(
            "record_repository_link",
            _REPOSITORIES,
            link.project_id,
            link.repository_id,
            link.linked,
            link.revision,
            from_revision,
            change,
        )
        return link

    def _create(self, operation: str, kind: LinkKind, project_id: str, name: str, change: ConfigChange) -> None:
        try:
            with self._store.write(operation, expect=(IntegrityError,)) as conn:
                insert_link(conn, kind, project_id, name, change)
        except IntegrityError as exc:  # a concurrent first link of the same pair landed first
            raise ConfigRevisionConflict(kind.label, f"{project_id}/{name}", current=1) from exc

    def _record(
        self,
        operation: str,
        kind: LinkKind,
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

    def source_link(self, project_id: ProjectId, work_source_id: WorkSourceId) -> SourceLink | None:
        return self.source_links_for([(project_id, work_source_id)]).get((project_id, work_source_id))

    def repository_link(self, project_id: ProjectId, repository_id: RepositoryId) -> RepositoryLink | None:
        return self.repository_links_for([(project_id, repository_id)]).get((project_id, repository_id))

    def sources_of(self, project_id: ProjectId) -> list[SourceLink]:
        return self.sources_of_projects([project_id]).get(project_id, [])

    def repositories_of(self, project_id: ProjectId) -> list[RepositoryLink]:
        return self.repositories_of_projects([project_id]).get(project_id, [])

    def projects_linking_source(self, work_source_id: str) -> list[ProjectId]:
        return self.projects_linking_sources([work_source_id]).get(work_source_id, [])

    def source_links_for(
        self, pairs: Sequence[tuple[ProjectId, WorkSourceId]]
    ) -> dict[tuple[ProjectId, WorkSourceId], SourceLink]:
        return {
            (ProjectId(project_id), WorkSourceId(name)): SourceLink(
                project_id=ProjectId(project_id), work_source_id=WorkSourceId(name), revision=revision, linked=linked
            )
            for (project_id, name), (revision, linked) in self._stored("source_links_for", _SOURCES, pairs).items()
        }

    def repository_links_for(
        self, pairs: Sequence[tuple[ProjectId, RepositoryId]]
    ) -> dict[tuple[ProjectId, RepositoryId], RepositoryLink]:
        stored = self._stored("repository_links_for", _REPOSITORIES, pairs)
        positions = self._positions("repository_links_for", set(stored))
        return {
            (ProjectId(project_id), RepositoryId(name)): RepositoryLink(
                project_id=ProjectId(project_id),
                repository_id=RepositoryId(name),
                revision=revision,
                linked=linked,
                position=positions.get((project_id, name)),
            )
            for (project_id, name), (revision, linked) in stored.items()
        }

    def sources_of_projects(self, project_ids: Sequence[ProjectId]) -> dict[ProjectId, list[SourceLink]]:
        return {
            ProjectId(project_id): [
                SourceLink(project_id=ProjectId(project_id), work_source_id=WorkSourceId(name), revision=revision)
                for name, revision in standing
            ]
            for project_id, standing in self._standing_of("sources_of_projects", _SOURCES, project_ids).items()
        }

    def repositories_of_projects(self, project_ids: Sequence[ProjectId]) -> dict[ProjectId, list[RepositoryLink]]:
        standing_of = self._standing_of("repositories_of_projects", _REPOSITORIES, project_ids)
        positions = self._positions(
            "repositories_of_projects",
            {(project_id, name) for project_id, standing in standing_of.items() for name, _ in standing},
        )
        return {
            ProjectId(project_id): [
                RepositoryLink(
                    project_id=ProjectId(project_id),
                    repository_id=RepositoryId(name),
                    revision=revision,
                    position=positions.get((project_id, name)),
                )
                for name, revision in standing
            ]
            for project_id, standing in standing_of.items()
        }

    def projects_linking_sources(self, work_source_ids: Sequence[str]) -> dict[str, list[ProjectId]]:
        if not work_source_ids:
            return {}
        with self._store.read("projects_linking_sources") as conn:
            return projects_linking_sources_conn(conn, work_source_ids)

    def linked_at(self, project_id: ProjectId, source: str, at: datetime) -> bool:
        if is_built_in(source):
            return True
        with self._store.read("linked_at") as conn:
            return _newest_linked(self._store, conn, _SOURCES, project_id, source, at=at)

    def _stored(
        self, operation: str, kind: LinkKind, pairs: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], tuple[int, bool]]:
        """Each stored link of ``pairs`` as its revision and whether it stands; a pair never linked is dropped."""
        if not pairs:
            return {}
        with self._store.read(operation) as conn:
            revisions = _revisions(self._store, conn, kind, set(pairs))
            if not revisions:
                return {}
            linked = _newest(
                self._store,
                conn,
                kind,
                sorted({project_id for project_id, _ in revisions}),
                sorted({name for _, name in revisions}),
                standing_only=False,
            )
        return {pair: (revision, linked.get(pair, False)) for pair, revision in revisions.items()}

    def _positions(self, operation: str, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], float | None]:
        """The newest landing position of each repository link of ``pairs`` that has one."""
        if not pairs:
            return {}
        with self._store.read(operation) as conn:
            return newest_positions(conn, pairs)

    def _standing_of(
        self, operation: str, kind: LinkKind, project_ids: Sequence[str]
    ) -> dict[str, list[tuple[str, int]]]:
        """Each project's standing links as ``(name, revision)``, ordered by name — a project with none dropped: the
        links its newest facts read linked, then those links' revisions."""
        if not project_ids:
            return {}
        found: dict[str, list[tuple[str, int]]] = {}
        with self._store.read(operation) as conn:
            standing = _standing(self._store, conn, kind, list(dict.fromkeys(project_ids)))
            for (project_id, name), revision in _revisions(self._store, conn, kind, standing).items():
                found.setdefault(project_id, []).append((name, revision))
        return {project_id: sorted(held) for project_id, held in found.items()}


_link_store_conforms: type[IWriteProjectLinks] = ProjectLinkStore
