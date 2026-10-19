"""The projects this runner serves, as its hub last answered.

The runner's configuration names the projects it serves, by id or slug, and every registration sends
that list — or ``None`` when the configuration names none, which keeps whatever declaration the hub
holds. The hub answers with how it resolved each declared entry, and the runner keeps that answer
whole in its store, as it keeps the hub's pause, so its status can say what it serves — and mark an
entry no project answers to — while the hub is unreachable. A reply naming no projects, from a hub
that predates them, keeps the stored answer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class ServedProject:
    """One declared entry as the hub resolved it: the ``entry`` as declared — a project id or slug —
    and the project it names in the runner's tenant, with its palette ``color``. ``project_id``,
    ``slug``, ``name`` and ``color`` are ``None`` for an entry no project answers to, which serves
    nothing — and ``color`` alone for a project answered by a hub that predates it."""

    entry: str
    project_id: str | None = None
    slug: str | None = None
    name: str | None = None
    color: str | None = None

    def resolved(self) -> bool:
        """Whether a project answers to the entry."""
        return self.project_id is not None


@domain_model
@dataclass(frozen=True)
class ServedProjectsAnswer:
    """The hub's latest answer to the runner's declaration, in declared order, and when the runner
    stored it by its own clock. An empty ``projects`` is an answer: the runner serves nothing."""

    projects: tuple[ServedProject, ...]
    answered_at: datetime


@domain_model
@dataclass(frozen=True)
class ServedProjectStatus:
    """One line of what the runner serves, for its status: an answered entry, resolved or not, or a
    configured entry the hub has not answered yet (``answered`` false, every project field ``None``)."""

    entry: str
    project_id: str | None = None
    slug: str | None = None
    name: str | None = None
    color: str | None = None
    answered: bool = True

    def resolved(self) -> bool:
        """Whether a project answers to the entry."""
        return self.project_id is not None


def served_projects_status(
    configured: Sequence[str] | None, answer: ServedProjectsAnswer | None
) -> tuple[ServedProjectStatus, ...] | None:
    """What the runner serves, for its status: every entry of the stored answer, then every configured
    entry the answer does not hold, as not yet answered. ``None`` when the configuration names no
    projects and the hub has answered none — nothing is known to show."""
    if answer is None and configured is None:
        return None
    answered = tuple(
        ServedProjectStatus(entry=p.entry, project_id=p.project_id, slug=p.slug, name=p.name, color=p.color)
        for p in (answer.projects if answer is not None else ())
    )
    held = {p.entry for p in answered}
    pending = tuple(
        ServedProjectStatus(entry=entry, answered=False)
        for entry in dict.fromkeys(configured or ())
        if entry not in held
    )
    return answered + pending


class IReadServedProjectsRepository(Protocol):
    """Read the hub's latest answer to the runner's served-projects declaration."""

    def served_projects_answer(self) -> ServedProjectsAnswer | None:
        """The latest stored answer, or ``None`` when no hub has answered one since the store was
        created."""
        ...


class IWriteServedProjectsRepository(IReadServedProjectsRepository, Protocol):
    """Read-write access to the stored answer — held only by the registration step."""

    def record_served_projects(self, projects: Sequence[ServedProject], *, at: datetime) -> None:
        """Replace the stored answer with ``projects``, whole, in one transaction."""
        ...


__all__ = [
    "IReadServedProjectsRepository",
    "IWriteServedProjectsRepository",
    "ServedProject",
    "ServedProjectStatus",
    "ServedProjectsAnswer",
    "served_projects_status",
]
