"""Hub items minting into a project (component tier): the operator's create names one or acts in the tenant's only
one, a routine run mints into the routine's project and an accepted proposal into the proposal's — the item row and
its chunk alike, never inferred from the hub source, which every project draws from."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import HubHarness, build_hub, default_project, default_tenant, hub_store_connections, seed_project

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

_GRAPH = """
name: alpha
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""


def _projects_of(hub: HubHarness, source: str, ref: str, chunk_id: str) -> tuple[str, ProjectId | None]:
    """The project the ``work_items`` row at ``source:ref`` records, read raw, and the one its chunk carries."""
    items = s.work_items
    with hub.engine.connect() as conn:
        item_project = conn.execute(
            sa.select(items.c.project_id).where(
                items.c.tenant_id == default_tenant(hub.engine), items.c.source == source, items.c.ref == ref
            )
        ).scalar_one()
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    return item_project, chunk.project_id


def _create_item(hub: HubHarness, *, project: str | None = None) -> tuple[int, dict]:  # type: ignore[type-arg]
    params = {"project": project} if project is not None else {}
    resp = hub.client.post("/api/work-sources/hub/items", params=params, json={"title": "t", "body": "b"})
    return resp.status_code, resp.json()


def test_an_operator_create_naming_a_project_mints_the_item_and_its_chunk_there(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    second = seed_project(hub.engine, "second")

    status, body = _create_item(hub, project="second")

    assert status == 201, body
    assert _projects_of(hub, body["source"], body["ref"], body["chunk_id"]) == (second, second)


def test_an_operator_create_naming_no_project_acts_in_the_tenants_only_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    status, body = _create_item(hub)

    assert status == 201, body
    only = default_project(hub.engine)
    assert _projects_of(hub, body["source"], body["ref"], body["chunk_id"]) == (only, only)


def test_an_operator_create_naming_no_project_in_a_two_project_tenant_is_project_required(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_project(hub.engine, "second")

    status, body = _create_item(hub)

    assert status == 422
    assert body["detail"]["code"] == "project_required"
    assert sorted(p["slug"] for p in body["detail"]["projects"]) == ["default", "second"]
    with hub.engine.connect() as conn:
        items = sa.select(sa.func.count()).select_from(s.work_items)
        assert conn.execute(items.where(s.work_items.c.tenant_id == default_tenant(hub.engine))).scalar_one() == 0


def test_an_operator_create_naming_an_unknown_project_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    status, body = _create_item(hub, project="nowhere")

    assert status == 422
    assert body["detail"][0]["loc"] == ["query", "project"]


def test_a_routine_run_mints_into_the_routines_project_though_the_tenant_holds_two(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH}).status_code == 201
    routine = hub.client.post(
        "/api/routines",
        json={"name": "gardening", "graph_name": "alpha", "default_scope_slug": "blizzard", "default_model": []},
    ).json()
    own = default_project(hub.engine)
    seed_project(hub.engine, "second")

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert _projects_of(hub, body["source"], body["ref"], body["chunk_id"]) == (own, own)


def test_an_accepted_proposals_item_mints_into_the_proposals_project(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    second = seed_project(hub.engine, "second")
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_1",
        project_id=second,
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=[],
        at=_NOW,
    )

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    closure = body["closure"]
    assert _projects_of(hub, closure["source"], closure["ref"], body["chunk_id"]) == (second, second)
