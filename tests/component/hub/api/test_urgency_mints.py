"""Every mint writes the tenant's default urgency on its chunk (component tier): an ingest, a hub item's create, a
garden proposal's accept, a routine's run, and a cadence's errand mint — and a mint after a settings edit writes the
new default."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.domain.cadence.errands import errands
from blizzard.hub.domain.cadence.model import CadenceShape, ChoreShape
from blizzard.hub.domain.cadence.services import CadenceAttention
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import ScopeSlug
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_project,
    default_urgency,
    hub_scoped,
    hub_store_connections,
    ingest,
    project_record,
)

_CTX = ChangeContext(actor="operator", door=Door.API)
_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _urgencies(hub: HubHarness) -> dict[str, str | None]:
    with hub.engine.connect() as conn:
        rows = conn.execute(hub_scoped(sa.select(s.chunks.c.chunk_id, s.chunks.c.urgency_id))).all()
    return {row.chunk_id: row.urgency_id for row in rows}


def _routine(hub: HubHarness, slug: str = "nightly") -> str:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    routine = hub.services.routine_authoring.create(
        project=project_record(hub, default_project(hub.engine)),
        ctx=_CTX,
        slug=slug,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(slug),
    )
    return routine.routine_id


def test_an_ingest_writes_the_default(hub: HubHarness) -> None:
    chunk_id = ingest(hub, [{"source": "hub", "ref": "1"}], promote=False)
    assert _urgencies(hub) == {chunk_id: default_urgency(hub.engine)}


def test_a_hub_item_create_writes_the_default(hub: HubHarness) -> None:
    resp = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"})
    assert resp.status_code == 201, resp.text
    assert set(_urgencies(hub).values()) == {default_urgency(hub.engine)}


def test_a_garden_proposal_accept_writes_the_default(hub: HubHarness) -> None:
    hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_1",
        project_id=default_project(hub.engine),
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the proposal's own body",
        findings=[],
        at=_NOW,
    )
    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})
    assert resp.status_code < 300, resp.text
    (minted,) = _urgencies(hub).values()
    assert minted == default_urgency(hub.engine)


def test_a_routine_run_writes_the_default(hub: HubHarness) -> None:
    resp = hub.client.post(f"/api/routines/{_routine(hub)}/run", json={})
    assert resp.status_code < 300, resp.text
    (minted,) = _urgencies(hub).values()
    assert minted == default_urgency(hub.engine)


def test_a_cadence_mint_writes_the_default(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    project = project_record(hub, default_project(hub.engine))
    cadence = hub.services.cadence_authoring.create(
        CadenceShape(slug="nightly", spacing="1h", chores=(ChoreShape("plain", "sweep", promote=True),)),
        project=project,
        ctx=_CTX,
        may_admit=True,
    )
    attention = CadenceAttention(
        routines=hub.services.routines,
        routine_scopes=hub.services.routine_scopes,
        scopes=hub.services.scopes,
        graphs=hub.services.graphs,
    )
    facts = attention.facts([cadence])[cadence.project_id]
    (errand,) = errands(cadence, facts)
    inputs = hub.services.errand_planning.inputs([(errand, facts)])

    minted = hub.services.cadence_mint.mint(errand, judged=None, inputs=inputs[errand.key])

    assert minted is not None and minted.promoted
    assert minted.chunk.urgency_id == default_urgency(hub.engine)
    assert _urgencies(hub) == {minted.chunk.chunk_id: default_urgency(hub.engine)}


def test_a_mint_after_a_settings_edit_writes_the_new_default(hub: HubHarness) -> None:
    before = ingest(hub, [{"source": "hub", "ref": "1"}], promote=False)
    standard = default_urgency(hub.engine)
    urgent = hub.client.post("/api/urgencies", json={"slug": "urgent"}).json()["urgency_id"]
    edited = hub.client.patch("/api/settings", json={"default_urgency": "urgent"})
    assert edited.status_code == 200, edited.text

    after = ingest(hub, [{"source": "hub", "ref": "2"}], promote=False)

    assert default_urgency(hub.engine) == urgent
    assert _urgencies(hub) == {before: standard, after: urgent}
