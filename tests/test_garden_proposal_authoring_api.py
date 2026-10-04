"""Garden-proposal authoring routes — create/edit/attach/detach
(component tier). Seeded straight through ``GardenProposalStore``/``FindingStore``/
``RoutineStore``, the ``tests/test_garden_proposal_api.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.garden_proposals import GardenProposalClosureKind, GardenProposalOrigin
from blizzard.foundation.ids import ROUTINE_PREFIX, Id
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import insert_garden_proposal_closure_row
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from tests.support import HubHarness, build_hub, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_scope(hub: HubHarness, slug: str = "blizzard") -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_NOW))


def _seed_routine(hub: HubHarness, name: str = "nightly", *, default_scope_slug: str = "blizzard") -> None:
    RoutineStore(hub_store_connections(hub.engine)).create(
        Routine(
            routine_id=Id.mint_at(ROUTINE_PREFIX, _NOW).value,
            name=name,
            graph_name="g",
            default_scope_slug=default_scope_slug,
            created_at=_NOW,
        )
    )


def _seed_finding(
    hub: HubHarness,
    finding_id: str,
    *,
    routine_name: str = "nightly",
    scope_slug: str = "blizzard",
    state: str = "live",
) -> None:
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name=routine_name,
        scope_slug=scope_slug,
        class_="stale-docstring",
        locus=f"{finding_id}.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )
    if state != "live":
        FindingStore(hub_store_connections(hub.engine)).record_fact(finding_id, kind=state, at=_NOW, note="n")


def _seed_proposal(
    hub: HubHarness, proposal_id: str = "gprop_1", *, findings: list[str] | None = None, closed: bool = False
) -> None:
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        proposal_id,
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=findings if findings is not None else [],
        at=_NOW,
    )
    if closed:
        with hub.engine.begin() as conn:
            insert_garden_proposal_closure_row(
                conn,
                proposal_id=proposal_id,
                closure=GardenProposalClosureKind.PASSED,
                reason="not worth it",
                closed_by="operator",
                at=_NOW,
                item_outcome=None,
                pointer=None,
            )


# --------------------------------------------------------------------------- #
# POST /garden-proposals


def test_create_mints_an_operator_proposal_naming_no_routine(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post(
        "/api/garden-proposals",
        json={"title": "t", "class": "c", "body": "b", "findings": ["fin_1"]},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["origin"] == "operator"
    assert body["created_by"] == "operator"
    assert body["routine_name"] is None
    assert body["findings"] == ["fin_1"]


def test_create_names_the_given_routine(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_routine(hub, "nightly")

    resp = hub.client.post(
        "/api/garden-proposals", json={"title": "t", "class": "c", "body": "b", "routine": "nightly"}
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["routine_name"] == "nightly"


def test_create_an_unknown_routine_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/garden-proposals", json={"title": "t", "class": "c", "body": "b", "routine": "ghost"})

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals").json()["proposals"] == []


def test_create_an_unknown_finding_id_is_422_and_creates_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post(
        "/api/garden-proposals", json={"title": "t", "class": "c", "body": "b", "findings": ["fin_ghost"]}
    )

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals").json()["proposals"] == []


def test_create_an_exited_finding_is_422_and_creates_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1", state="wont-fix")

    resp = hub.client.post(
        "/api/garden-proposals", json={"title": "t", "class": "c", "body": "b", "findings": ["fin_1"]}
    )

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals").json()["proposals"] == []


def test_create_naming_a_delivered_finding_succeeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1", state="delivered")

    resp = hub.client.post(
        "/api/garden-proposals", json={"title": "t", "class": "c", "body": "b", "findings": ["fin_1"]}
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["findings"] == ["fin_1"]


def test_create_the_same_finding_named_twice_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")

    resp = hub.client.post(
        "/api/garden-proposals", json={"title": "t", "class": "c", "body": "b", "findings": ["fin_1", "fin_1"]}
    )

    assert resp.status_code == 422, resp.text


def test_create_a_blank_title_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/garden-proposals", json={"title": "  ", "class": "c", "body": "b"})

    assert resp.status_code == 422, resp.text


def test_create_a_finding_from_a_different_routine_and_scope_than_the_named_routine(tmp_path: Path) -> None:
    """A proposal carries no scope: its findings may span any routines/scopes."""
    hub = build_hub(tmp_path)
    _seed_scope(hub, "blizzard")
    _seed_scope(hub, "runner")
    _seed_routine(hub, "nightly", default_scope_slug="blizzard")
    _seed_finding(hub, "fin_1", routine_name="weekly", scope_slug="runner")

    resp = hub.client.post(
        "/api/garden-proposals",
        json={"title": "t", "class": "c", "body": "b", "routine": "nightly", "findings": ["fin_1"]},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["findings"] == ["fin_1"]


# --------------------------------------------------------------------------- #
# PATCH /garden-proposals/{id}


def test_edit_replaces_only_the_given_fields(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub)

    resp = hub.client.patch("/api/garden-proposals/gprop_1", json={"title": "new title"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "new title"
    assert body["class"] == "fix-the-source"  # unchanged
    assert body["body"] == "the case"  # unchanged


def test_edit_an_unknown_proposal_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.patch("/api/garden-proposals/gprop_ghost", json={"title": "t"})

    assert resp.status_code == 404, resp.text


def test_edit_a_closed_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub, closed=True)

    resp = hub.client.patch("/api/garden-proposals/gprop_1", json={"title": "new title"})

    assert resp.status_code == 409, resp.text


def test_edit_an_accepted_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub)
    hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False})

    resp = hub.client.patch("/api/garden-proposals/gprop_1", json={"title": "new title"})

    assert resp.status_code == 409, resp.text


def test_edit_an_operator_authored_proposal_while_open(tmp_path: Path) -> None:
    """`edit` works on either origin while open."""
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    created = hub.client.post("/api/garden-proposals", json={"title": "t", "class": "c", "body": "b"})
    proposal_id = created.json()["proposal_id"]

    resp = hub.client.patch(f"/api/garden-proposals/{proposal_id}", json={"title": "new title"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "new title"
    assert resp.json()["origin"] == "operator"


def test_edit_naming_no_field_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub)

    resp = hub.client.patch("/api/garden-proposals/gprop_1", json={})

    assert resp.status_code == 422, resp.text


def test_edit_a_blank_title_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub)

    resp = hub.client.patch("/api/garden-proposals/gprop_1", json={"title": "  "})

    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# POST /garden-proposals/{id}/attach


def test_attach_links_the_given_findings(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_2"]})

    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["findings"]) == ["fin_1", "fin_2"]


def test_attach_an_unknown_proposal_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/garden-proposals/gprop_ghost/attach", json={"findings": ["fin_1"]})

    assert resp.status_code == 404, resp.text


def test_attach_a_closed_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, findings=["fin_1"], closed=True)

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_2"]})

    assert resp.status_code == 409, resp.text


def test_attach_an_accepted_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, findings=["fin_1"])
    hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False})

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_2"]})

    assert resp.status_code == 409, resp.text


def test_attach_an_unknown_finding_id_is_422_and_links_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_ghost"]})

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["findings"] == ["fin_1"]


def test_attach_an_exited_finding_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_2", state="wont-fix")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_2"]})

    assert resp.status_code == 422, resp.text


def test_attach_a_delivered_finding_succeeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_2", state="delivered")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_2"]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["findings"] == ["fin_1", "fin_2"]


def test_attach_a_finding_already_linked_to_this_proposal_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_1"]})

    assert resp.status_code == 422, resp.text


def test_attach_the_same_finding_named_twice_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_2", "fin_2"]})

    assert resp.status_code == 422, resp.text


def test_attach_to_a_finding_already_linked_to_another_proposal_is_allowed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, "gprop_1", findings=["fin_1"])
    _seed_proposal(hub, "gprop_2", findings=[])

    resp = hub.client.post("/api/garden-proposals/gprop_2/attach", json={"findings": ["fin_1"]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["findings"] == ["fin_1"]


# --------------------------------------------------------------------------- #
# POST /garden-proposals/{id}/detach


def test_detach_unlinks_the_given_findings(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, findings=["fin_1", "fin_2"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_2"]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["findings"] == ["fin_1"]


def test_detach_an_unknown_proposal_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/garden-proposals/gprop_ghost/detach", json={"findings": ["fin_1"]})

    assert resp.status_code == 404, resp.text


def test_detach_a_closed_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, findings=["fin_1"], closed=True)

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_1"]})

    assert resp.status_code == 409, resp.text


def test_detach_an_accepted_proposal_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, findings=["fin_1"])
    hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False})

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_1"]})

    assert resp.status_code == 409, resp.text


def test_detach_a_finding_not_linked_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_ghost"]})

    assert resp.status_code == 422, resp.text
    assert "unknown finding id" in resp.json()["detail"]
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["findings"] == ["fin_1"]


def test_detach_a_known_finding_not_linked_to_this_proposal_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_finding(hub, "fin_2")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_2"]})

    assert resp.status_code == 422, resp.text
    assert "not linked" in resp.json()["detail"]
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["findings"] == ["fin_1"]


def test_detach_the_same_finding_named_twice_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_1", "fin_1"]})

    assert resp.status_code == 422, resp.text


def test_detach_does_not_require_liveness(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_scope(hub)
    _seed_finding(hub, "fin_1", state="wont-fix")
    _seed_proposal(hub, findings=["fin_1"])

    resp = hub.client.post("/api/garden-proposals/gprop_1/detach", json={"findings": ["fin_1"]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["findings"] == []
