"""SQLAlchemy adapter for a project's landing order (package-private).

A repository link's place in its project's landing order is its newest fact in ``project_repository_positions``
(``bzh:facts-not-status``). A fact names a link, so an unlinked link's facts stay in place, ignored, and a relink reads
them again. A position write is not a revision of the link and appends no change row."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.projects.order import IWriteProjectPositions, PositionFact, RankedLink, in_landing_order
from blizzard.hub.store.schema import project_repository_positions, repositories
from blizzard.hub.store.scoped import ScopedConnections, statements_of


def newest_positions(conn: Connection, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], float | None]:
    """The newest position of each ``(project_id, repository_id)`` link of ``pairs`` that has a fact, read on the
    caller's connection. One group-by-max join per batch (``bzh:newest-per-key-read``)."""
    stmts = statements_of(conn)
    facts = project_repository_positions
    found: dict[tuple[str, str], float | None] = {}
    for project_batch in id_batches(sorted({project_id for project_id, _ in pairs})):
        for name_batch in id_batches(sorted({name for _, name in pairs})):
            newest = (
                stmts.select(facts, facts.c.project_id, facts.c.repository_id, func.max(facts.c.id).label("id"))
                .where(facts.c.project_id.in_(project_batch), facts.c.repository_id.in_(name_batch))
                .group_by(facts.c.project_id, facts.c.repository_id)
                .subquery()
            )
            rows = conn.execute(
                stmts.select(facts, facts.c.project_id, facts.c.repository_id, facts.c.position).join(
                    newest, facts.c.id == newest.c.id
                )
            ).all()
            found.update(((row[0], row[1]), row[2]) for row in rows if (row[0], row[1]) in pairs)
    return found


def _slugs(conn: Connection, repository_ids: Sequence[str]) -> dict[str, str]:
    stmts = statements_of(conn)
    found: dict[str, str] = {}
    for batch in id_batches(list(repository_ids)):
        rows = conn.execute(
            stmts.select(repositories, repositories.c.repository_id, repositories.c.slug).where(
                repositories.c.repository_id.in_(batch)
            )
        ).all()
        found.update((row[0], row[1]) for row in rows)
    return found


class ProjectPositionStore:
    """Read-write landing-order adapter over the hub store; a project's standing repository links, each carrying its
    position, are read through ``links``."""

    def __init__(self, store: ScopedConnections, *, links: IReadProjectLinks) -> None:
        self._store = store
        self._links = links

    def landing_links(self, project_id: ProjectId) -> list[RankedLink]:
        standing = self._links.repositories_of(project_id)
        if not standing:
            return []
        with self._store.read("landing_links") as conn:
            slugs = _slugs(conn, [link.repository_id for link in standing])
        return in_landing_order(
            [
                RankedLink(RepositoryId(link.repository_id), slugs[link.repository_id], link.position)
                for link in standing
            ]
        )

    def record_positions(self, project_id: ProjectId, facts: Sequence[PositionFact], *, at: datetime, by: str) -> None:
        if not facts:
            return
        with self._store.write("record_positions") as conn:
            conn.execute(
                statements_of(conn).insert_rows(
                    project_repository_positions,
                    [
                        {
                            "project_id": project_id,
                            "repository_id": fact.repository_id,
                            "position": fact.position,
                            "set_at": at,
                            "set_by": by,
                        }
                        for fact in facts
                    ],
                )
            )


_position_store_conforms: type[IWriteProjectPositions] = ProjectPositionStore
