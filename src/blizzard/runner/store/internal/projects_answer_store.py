"""SQLAlchemy adapter for the served-projects answer seam (package-private)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.served_projects import IWriteServedProjectsRepository, ServedProject, ServedProjectsAnswer
from blizzard.runner.store.schema import hub_served_projects, runner_identity
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class ServedProjectsStore:
    """Read-write adapter over the hub's latest served-projects answer: its rows, and the identity
    row's marker saying an answer was stored."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def served_projects_answer(self) -> ServedProjectsAnswer | None:
        marker = self._store.all(
            self._store.select(runner_identity, runner_identity.c.projects_answered_at)
            .order_by(runner_identity.c.id.desc())
            .limit(1)
        )
        if not marker or marker[0].projects_answered_at is None:
            return None
        rows = self._store.all(
            self._store.select(
                hub_served_projects,
                hub_served_projects.c.entry,
                hub_served_projects.c.project_id,
                hub_served_projects.c.slug,
                hub_served_projects.c.name,
                hub_served_projects.c.color,
            ).order_by(hub_served_projects.c.position)
        )
        return ServedProjectsAnswer(
            projects=tuple(
                ServedProject(entry=str(r.entry), project_id=r.project_id, slug=r.slug, name=r.name, color=r.color)
                for r in rows
            ),
            answered_at=marker[0].projects_answered_at,
        )

    def record_served_projects(self, projects: Sequence[ServedProject], *, at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(self._store.delete(hub_served_projects))
            if projects:
                conn.execute(
                    self._store.insert(hub_served_projects),
                    [
                        {
                            "position": position,
                            "entry": p.entry,
                            "project_id": p.project_id,
                            "slug": p.slug,
                            "name": p.name,
                            "color": p.color,
                            "answered_at": at,
                        }
                        for position, p in enumerate(projects)
                    ],
                )
            conn.execute(self._store.update(runner_identity).values(projects_answered_at=at))
        _log.debug(
            "served projects recorded",
            entries=[p.entry for p in projects],
            unresolved=[p.entry for p in projects if not p.resolved()],
        )


def _conforms_served_projects_store(x: ServedProjectsStore) -> IWriteServedProjectsRepository:
    return x
