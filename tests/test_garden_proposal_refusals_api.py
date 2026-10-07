"""Garden-proposal refusals at the routes (component tier): a closed proposal answers
409 ahead of every other refusal, an accept minting nothing takes no body, an accept's
blank reason is stored as none, and attach/detach must name a finding."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import HubHarness, build_hub, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed(hub: HubHarness) -> None:
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="blizzard", description="", created_at=_NOW))
    FindingStore(hub_store_connections(hub.engine)).add(
        "fin_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_NOW,
    )
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_1",
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=["fin_1"],
        at=_NOW,
    )


def test_passing_a_closed_proposal_with_a_blank_reason_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "not worth it"})

    resp = hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "   "})

    assert resp.status_code == 409, resp.text


def test_accepting_without_minting_but_with_a_body_is_422_and_closes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post(
        "/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False, "body": "never lands"}
    )

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["closure"] is None


def test_a_body_without_mint_on_a_closed_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "not worth it"})

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False, "body": "b"})

    assert resp.status_code == 409, resp.text


@pytest.mark.parametrize("mint", [False, True])
def test_an_accepts_blank_reason_is_stored_as_none(tmp_path: Path, mint: bool) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": mint, "reason": "   "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["closure"]["reason"] is None


def test_an_accepts_reason_is_stored_stripped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post(
        "/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False, "reason": "  by hand "}
    )

    assert resp.json()["closure"]["reason"] == "by hand"


@pytest.mark.parametrize("verb", ["attach", "detach"])
def test_attach_or_detach_naming_no_finding_is_422(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post(f"/api/garden-proposals/gprop_1/{verb}", json={"findings": []})

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["findings"] == ["fin_1"]


@pytest.mark.parametrize("verb", ["attach", "detach"])
def test_attach_or_detach_naming_no_finding_on_a_closed_proposal_is_409(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "not worth it"})

    resp = hub.client.post(f"/api/garden-proposals/gprop_1/{verb}", json={"findings": []})

    assert resp.status_code == 409, resp.text


def test_accepting_with_the_default_graph_retired_is_503_and_closes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    hub.services.graph_lifecycle.retire(graph, by="operator")

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": True})

    assert resp.status_code == 503, resp.text
    assert hub.services.default_graph_doc.name in resp.json()["detail"]
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["closure"] is None
