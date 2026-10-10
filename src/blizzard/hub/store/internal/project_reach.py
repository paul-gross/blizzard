"""What a project reaches through its chunks (package-private).

A chunk-owned row — a usage fact, an event, a route — carries no project of its own: it reaches one
through its chunk's ``project_id``, the column every chunk is minted with and never changes. A read
narrowed to a project selects the rows whose chunk is among :func:`chunks_of_project`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedStatements


def chunks_of_project(stmts: ScopedStatements, project: ProjectId) -> Select[Any]:
    """The ids of ``project``'s chunks within ``stmts``' tenant, as a subquery for ``chunk_id IN (…)``."""
    return stmts.select(s.chunks, s.chunks.c.chunk_id).where(s.chunks.c.project_id == project)
