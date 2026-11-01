"""A garden proposal cites only its own project's findings (component tier): the domain refuses an operator create and
an attach naming a finding of another project, the create under a project and the attach routes answer ``422`` naming
it, the proposal store refuses the same links beneath the domain, and a delivery citing another project's finding is
rejected naming it, with nothing written.

Every store here holds two projects in its one tenant, so every write names its project and no call relies on the
sole-project fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import DeliveryPlan, NewProposal
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.proposals.model import GardenProposal, GardenProposalFindingOtherProjectError
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.kernel.garden_delivery_fault import GardenDeliveryFault
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import (
    HubHarness,
    build_hub,
    default_project,
    hub_scoped,
    hub_store_connections,
    seed_chunk,
    seed_graph,
    seed_project,
    stamp,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_OWN = "fin_01KX9SZA00AAAAAAAAAAAAAAAA"
_FOREIGN = "fin_01KX9SZA00BBBBBBBBBBBBBBBB"


def _seed_finding(hub: HubHarness, finding_id: str, *, project_id: str) -> Finding:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.scopes).values(
                stamp(s.scopes, slug=f"scope-{finding_id[-4:]}", description="", created_at=_NOW, project_id=project_id)
            )
        )
    return FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        project_id=project_id,
        routine_name="nightly",
        scope_slug=f"scope-{finding_id[-4:]}",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )


def _two_projects(tmp_path: Path) -> tuple[HubHarness, str, str]:
    """A hub whose tenant holds its default project, owning `_OWN` and the chunk `ch_1`, and a second project owning
    `_FOREIGN`; returns the hub and both project ids."""
    hub = build_hub(tmp_path)
    own = default_project(hub.engine)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
    _seed_finding(hub, _OWN, project_id=own)
    other = seed_project(hub.engine, "other")
    _seed_finding(hub, _FOREIGN, project_id=other)
    return hub, own, other


def _proposal_rows(hub: HubHarness) -> list[str]:
    with hub.engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(s.garden_proposals.c.proposal_id))).scalars())


def _create(store: GardenProposalStore, project_id: str, findings: list[str]) -> GardenProposal:
    return store.create(
        "gprop_1",
        origin=GardenProposalOrigin.OPERATOR,
        project_id=project_id,
        routine_name=None,
        created_by="operator",
        class_="c",
        title="t",
        body="b",
        findings=findings,
        at=_NOW,
    )


def test_an_operator_proposal_refuses_a_finding_of_another_project_naming_it(tmp_path: Path) -> None:
    hub, own, _ = _two_projects(tmp_path)
    found = FindingStore(hub_store_connections(hub.engine)).get_many([_OWN, _FOREIGN])

    with pytest.raises(GardenProposalFindingOtherProjectError, match=_FOREIGN):
        GardenProposal.operator(
            "gprop_1",
            project_id=own,
            created_by="operator",
            routine=None,
            class_="c",
            title="t",
            body="b",
            findings=[found[_OWN], found[_FOREIGN]],
            at=_NOW,
        )


def test_the_proposal_store_refuses_to_create_links_to_another_projects_finding(tmp_path: Path) -> None:
    hub, own, _ = _two_projects(tmp_path)
    store = GardenProposalStore(hub_store_connections(hub.engine))

    with pytest.raises(GardenProposalFindingOtherProjectError, match=_FOREIGN):
        _create(store, own, [_OWN, _FOREIGN])

    assert _proposal_rows(hub) == []


def test_the_proposal_store_refuses_to_attach_another_projects_finding(tmp_path: Path) -> None:
    hub, own, _ = _two_projects(tmp_path)
    store = GardenProposalStore(hub_store_connections(hub.engine))
    _create(store, own, [_OWN])

    with pytest.raises(GardenProposalFindingOtherProjectError, match=_FOREIGN):
        store.attach("gprop_1", [_FOREIGN])

    assert store.get("gprop_1").findings == [_OWN]  # type: ignore[union-attr]


def test_attaching_another_projects_finding_is_422_naming_it(tmp_path: Path) -> None:
    hub, own, _ = _two_projects(tmp_path)
    _create(GardenProposalStore(hub_store_connections(hub.engine)), own, [_OWN])

    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": [_FOREIGN]})

    assert resp.status_code == 422, resp.text
    assert _FOREIGN in resp.json()["detail"]
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["findings"] == [_OWN]


@pytest.mark.parametrize(
    ("project", "findings", "foreign"),
    [("default", [_OWN, _FOREIGN], _FOREIGN), ("other", [_FOREIGN, _OWN], _OWN)],
    ids=["default", "other"],
)
def test_creating_under_a_project_citing_another_projects_finding_is_422_naming_it(
    tmp_path: Path, project: str, findings: list[str], foreign: str
) -> None:
    hub, _, _ = _two_projects(tmp_path)
    body = {"class": "c", "title": "t", "body": "b", "findings": findings}

    resp = hub.client.post(f"/api/projects/{project}/garden-proposals", json=body)

    assert resp.status_code == 422, resp.text
    assert foreign in resp.json()["detail"]
    assert _proposal_rows(hub) == []


def test_creating_under_a_project_citing_its_own_finding_links_it(tmp_path: Path) -> None:
    hub, _, other = _two_projects(tmp_path)
    body = {"class": "c", "title": "t", "body": "b", "findings": [_FOREIGN]}

    resp = hub.client.post("/api/projects/other/garden-proposals", json=body)

    assert resp.status_code == 201, resp.text
    assert (resp.json()["project_id"], resp.json()["findings"]) == (other, [_FOREIGN])


def test_a_delivery_citing_another_projects_finding_is_rejected_naming_it(tmp_path: Path) -> None:
    hub, own, _ = _two_projects(tmp_path)
    plan = DeliveryPlan(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="garden-deliver",
        epoch=1,
        at=_NOW,
        run=RunContext(project_id=own, routine_name="nightly", scope_slug="blizzard", mode="full"),
        deltas=[],
        proposals=[
            NewProposal(
                proposal_id="gprop_1",
                routine_name="nightly",
                class_="fix-the-source",
                title="t",
                body="b",
                source_artifact_id="art_docket",
                ref="p1",
                finding_ids=[_OWN, _FOREIGN],
            )
        ],
    )

    with pytest.raises(GardenDeliveryRejected, match=_FOREIGN) as exc:
        GardenDeliveryStore(hub_store_connections(hub.engine)).deliver(plan, admission=EpochAdmission.AT_OR_ABOVE)
    assert exc.value.fault is GardenDeliveryFault.PROPOSALS

    assert _proposal_rows(hub) == []
