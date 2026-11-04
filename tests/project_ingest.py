"""Helpers for the ingest project api tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from blizzard.hub.domain.projects.model import ProjectId
from tests.support import (
    FakeWorkSource,
    HubHarness,
    build_hub,
    default_project,
    link_project,
    seed_fake_work_sources,
    seed_project,
)


def two_projects(tmp_path: Path) -> tuple[HubHarness, ProjectId, ProjectId]:
    """A hub whose ``first`` project draws from ``a`` and ``shared``, and whose ``second`` draws from ``b`` and
    ``shared``; ``loose`` is linked to neither."""
    names = ("a", "b", "shared", "loose")
    hub = build_hub(tmp_path, work_sources={name: FakeWorkSource(name=name) for name in names}, link=False)
    seed_fake_work_sources(hub.engine, names, tmp_path=tmp_path, link=False)
    first = ProjectId(default_project(hub.engine))
    second = seed_project(hub.engine, "second")
    for project, sources in ((first, ("a", "shared")), (second, ("b", "shared"))):
        for source in sources:
            link_project(hub.engine, project=project, source=source)
    return hub, first, second


def ingest_project(hub: HubHarness, *tokens: str, project: str | None = None) -> tuple[int, dict[str, Any]]:
    body: dict[str, object] = {"tokens": list(tokens)}
    if project is not None:
        body["project"] = project
    resp = hub.client.post("/api/chunks", json=body)
    decoded: dict[str, Any] = resp.json()
    return resp.status_code, decoded
