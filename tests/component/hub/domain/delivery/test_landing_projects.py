"""The landing-project loaders read in a bounded number of statements (`bzh:bulk-reconstitution`) and build each
:class:`LandingProject` from the loaded values."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from blizzard.hub.domain.actions.visit import LandingProject
from blizzard.hub.domain.delivery.landing_projects import read_landing_project, read_landing_projects
from blizzard.hub.domain.projects.model import ProjectId
from tests.support.linked_projects_world import N, build_links_hub, project_id, repository_id
from tests.support.position_facts import record_fact
from tests.support.scaffolding import HubHarness, count_queries


@pytest.fixture
def links_hub(tmp_path: Path) -> HubHarness:
    return build_links_hub(tmp_path)


def _read(hub: HubHarness, ids: list[ProjectId | None]) -> dict[ProjectId | None, LandingProject]:
    return read_landing_projects(ids, projects=hub.services.projects, links=hub.services.project_links)


def test_read_many_matches_read_per_project(links_hub: HubHarness) -> None:
    ids: list[ProjectId | None] = [project_id(links_hub, f"p{i}") for i in range(N)]
    record_fact(links_hub, str(ids[2]), repository_id(links_hub, "r1"), 3.0)
    missing = ProjectId("pr_missing")
    ids += [None, missing]

    many = _read(links_hub, ids)

    for pid in ids:
        assert many[pid] == read_landing_project(
            pid, projects=links_hub.services.projects, links=links_hub.services.project_links
        )
    assert many[None] == LandingProject()
    assert many[missing] == LandingProject(project_id=missing, slug=missing)
    assert many[ids[2]].linked  # a standing link set
    assert many[ids[0]].linked == {repository_id(links_hub, "r0")}


def test_read_many_statements_are_flat_across_distinct_projects(links_hub: HubHarness) -> None:
    ids = [project_id(links_hub, f"p{i}") for i in range(N)]
    reads: Callable[[int], object] = lambda n: _read(links_hub, list(ids[:n]))  # noqa: E731
    assert count_queries(links_hub.engine, lambda: reads(1)) == count_queries(links_hub.engine, lambda: reads(N))
    assert count_queries(links_hub.engine, lambda: _read(links_hub, [None])) == 0
