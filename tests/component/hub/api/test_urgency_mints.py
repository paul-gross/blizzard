"""Every mint writes the urgency its chain resolves on its chunk (component tier): an ingest, a hub item's create, a
garden proposal's accept, a routine's run, and a cadence's errand mint each take the tenant's default when nothing
names another, a stated urgency over it, and — for a run — the chore's override over the routine's default over the
tenant's. A mint after a settings edit writes the new default, and a stated urgency that is unknown or retired is
refused with nothing written."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.domain.cadence.errands import errands
from blizzard.hub.domain.cadence.mint import MintedErrand
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


def _work_items(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(hub_scoped(sa.select(sa.func.count()).select_from(s.work_items))).scalar_one()


def _urgency(hub: HubHarness, slug: str) -> str:
    resp = hub.client.post("/api/urgencies", json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["urgency_id"]


def _retired(hub: HubHarness, slug: str) -> str:
    urgency_id = _urgency(hub, slug)
    resp = hub.client.post(f"/api/urgencies/{slug}/retire", json={})
    assert resp.status_code == 200, resp.text
    return urgency_id


def _routine(hub: HubHarness, slug: str = "nightly", *, default_urgency: str | None = None) -> str:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    routine = hub.services.routine_authoring.create(
        project=project_record(hub, default_project(hub.engine)),
        ctx=_CTX,
        slug=slug,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(slug),
        default_urgency=None if default_urgency is None else hub.services.urgency_reading.named("u", default_urgency),
    )
    return routine.routine_id


def _proposal(hub: HubHarness) -> None:
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


def _cadence_mint(hub: HubHarness, *, chore_urgency: str | None) -> MintedErrand:
    project = project_record(hub, default_project(hub.engine))
    cadence = hub.services.cadence_authoring.create(
        CadenceShape(
            slug="nightly", spacing="1h", chores=(ChoreShape("plain", "sweep", promote=True, urgency=chore_urgency),)
        ),
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
    assert minted is not None
    return minted


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


# --- a stated urgency, and the run's chain ---------------------------------------------------------------------


def test_an_ingest_stating_an_urgency_by_slug_writes_it(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    resp = hub.client.post("/api/chunks", json={"tokens": ["hub:1"], "urgency": "urgent"})
    assert resp.status_code == 201, resp.text
    assert _urgencies(hub) == {resp.json()["chunk_id"]: urgent}


def test_a_hub_item_create_stating_an_urgency_by_id_writes_it(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    resp = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b", "urgency": urgent})
    assert resp.status_code == 201, resp.text
    assert _urgencies(hub) == {resp.json()["chunk_id"]: urgent}


def test_a_garden_proposal_accept_stating_an_urgency_writes_it(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    _proposal(hub)
    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"urgency": "urgent"})
    assert resp.status_code < 300, resp.text
    assert _urgencies(hub) == {resp.json()["chunk_id"]: urgent}


def test_an_accept_minting_nothing_refuses_an_urgency(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    _proposal(hub)
    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"urgency": "urgent", "mint_work_item": False})
    assert resp.status_code == 422, resp.text
    with hub.engine.connect() as conn:
        closures = conn.execute(
            hub_scoped(sa.select(sa.func.count()).select_from(s.garden_proposal_closures))
        ).scalar_one()
    assert closures == 0


def test_a_routine_run_takes_the_routine_default_and_a_stated_urgency_over_it(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    standard = default_urgency(hub.engine)
    routine_id = _routine(hub, default_urgency="urgent")

    by_default = hub.client.post(f"/api/routines/{routine_id}/run", json={})
    assert by_default.status_code < 300, by_default.text
    stated = hub.client.post(f"/api/routines/{routine_id}/run", json={"urgency": "standard"})
    assert stated.status_code < 300, stated.text

    assert _urgencies(hub) == {by_default.json()["chunk_id"]: urgent, stated.json()["chunk_id"]: standard}


def test_a_cadence_mint_takes_the_chore_override_over_the_routine_default(hub: HubHarness) -> None:
    _urgency(hub, "later")
    chore = _urgency(hub, "urgent")
    _routine(hub, "sweep", default_urgency="later")

    minted = _cadence_mint(hub, chore_urgency="urgent")

    assert _urgencies(hub) == {minted.chunk.chunk_id: chore}


def test_a_cadence_mint_with_no_override_takes_the_routine_default(hub: HubHarness) -> None:
    later = _urgency(hub, "later")
    _routine(hub, "sweep", default_urgency="later")

    minted = _cadence_mint(hub, chore_urgency=None)

    assert _urgencies(hub) == {minted.chunk.chunk_id: later}


# --- refusals write nothing ------------------------------------------------------------------------------------


def _mint_stating(hub: HubHarness, path: str, urgency: str) -> tuple[int, str]:
    """Post the mint ``path`` names, stating ``urgency``; its status and the refusal's type."""
    if path == "ingest":
        url, body = "/api/chunks", {"tokens": ["hub:1"]}
    elif path == "item":
        url, body = "/api/work-sources/hub/items", {"title": "t", "body": "b"}
    elif path == "accept":
        url, body = "/api/garden-proposals/gprop_1/accept", {}
    else:
        url, body = f"/api/routines/{_routine(hub)}/run", {}
    resp = hub.client.post(url, json={**body, "urgency": urgency})
    return resp.status_code, resp.json()["detail"][0]["type"]


@pytest.mark.parametrize("path", ["ingest", "item", "accept", "run"])
def test_a_stated_unknown_urgency_is_refused_with_nothing_written(hub: HubHarness, path: str) -> None:
    _proposal(hub)

    assert _mint_stating(hub, path, "nosuch") == (422, "urgency_unknown")
    assert _urgencies(hub) == {}
    assert _work_items(hub) == 0


@pytest.mark.parametrize("path", ["ingest", "item", "accept", "run"])
def test_a_stated_retired_urgency_is_refused_with_nothing_written(hub: HubHarness, path: str) -> None:
    _proposal(hub)
    _retired(hub, "gone")

    assert _mint_stating(hub, path, "gone") == (422, "urgency_retired")
    assert _urgencies(hub) == {}
    assert _work_items(hub) == 0
