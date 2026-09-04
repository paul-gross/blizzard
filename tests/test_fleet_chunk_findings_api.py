"""``GET /api/fleet/chunks/{chunk_id}/findings`` and its ``/{finding_id}`` sibling — the
worker's own per-chunk read of the findings its accepted, minted garden proposal answers
(blizzard#397 Phase 1, component tier). Distinct from ``get_garden_findings``'s
routine-run bucket: this route resolves through the chunk's own garden-proposal closure,
not its ``RunContext``, and refuses — rather than answering an empty bucket for — a
chunk answering no such proposal. ``get`` selects within that resolved set, so an
out-of-set id 404s the same as a chunk answering no proposal at all, the
``tests/test_fleet_garden_findings_api.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
_ROUTINE = "nightly"
_SCOPE = "blizzard"


def _seed_finding(hub: HubHarness, finding_id: str) -> None:
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        routine_name=_ROUTINE,
        scope_slug=_SCOPE,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=_NOW,
    )


def _seed_proposal(hub: HubHarness, *, proposal_id: str = "gprop_1", findings: list[str]) -> None:
    for finding_id in findings:
        _seed_finding(hub, finding_id)
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        proposal_id, routine_name=_ROUTINE, class_="fix-the-source", title="t", body="b", findings=findings, at=_NOW
    )


def _accept(hub: HubHarness, proposal_id: str = "gprop_1") -> str:
    resp = hub.client.post(f"/api/garden-proposals/{proposal_id}/accept", json={})
    assert resp.status_code == 200, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert chunk_id is not None
    return chunk_id


def _seed_chunk_answering_no_proposal(hub: HubHarness) -> str:
    """A plain hub work item, minted with no garden-proposal accept behind it."""
    item = seed_work_item(
        WorkItemStore(hub_store_connections(hub.engine)),
        graph_id="gr_garden",
        author=WorkItemAuthor.user("u_1"),
        at=_NOW,
    )
    return f"ch_{item.ref}"


# --------------------------------------------------------------------------- #
# GET /chunks/{chunk_id}/findings


def test_404s_on_an_unknown_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/fleet/chunks/ch_ghost/findings")
    assert resp.status_code == 404, resp.text
    assert "unknown chunk" in resp.json()["detail"]


def test_404s_on_a_chunk_answering_no_proposal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk_answering_no_proposal(hub)

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings")
    assert resp.status_code == 404, resp.text
    assert "no accepted, minted garden proposal" in resp.json()["detail"]


def test_returns_the_proposals_findings_via_the_shared_projection(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub, findings=["fin_1", "fin_2"])
    chunk_id = _accept(hub)

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings")
    assert resp.status_code == 200, resp.text
    assert [row["finding_id"] for row in resp.json()] == ["fin_1", "fin_2"]

    # The operator's own `GET /api/findings` reads through the identical projection —
    # the fleet route reuses it rather than restating it.
    operator = hub.client.get("/api/findings", params={"routine": _ROUTINE, "scope": _SCOPE}).json()
    by_id = {row["finding_id"]: row for row in operator}
    for row in resp.json():
        assert row == by_id[row["finding_id"]]


# --------------------------------------------------------------------------- #
# GET /chunks/{chunk_id}/findings/{finding_id}


def test_get_one_finding_within_the_answered_set(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub, findings=["fin_1", "fin_2"])
    chunk_id = _accept(hub)

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_2")
    assert resp.status_code == 200, resp.text
    assert resp.json()["finding_id"] == "fin_2"


def test_get_an_out_of_set_id_is_404_naming_the_cause(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed_proposal(hub, findings=["fin_1"])
    chunk_id = _accept(hub)
    _seed_finding(hub, "fin_other")  # exists, but answers no proposal this chunk carries

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_other")
    assert resp.status_code == 404, resp.text
    assert "not among the findings" in resp.json()["detail"]


def test_get_on_an_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/fleet/chunks/ch_ghost/findings/fin_1")
    assert resp.status_code == 404, resp.text
    assert "unknown chunk" in resp.json()["detail"]


def test_get_on_a_chunk_answering_no_proposal_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    # `fin_1` genuinely exists and answers a different chunk's proposal, pinning that
    # "this chunk answers no proposal" outranks "id not in this chunk's set" (D3) — a
    # never-seeded id would 404 the same way for either reason and prove nothing.
    _seed_proposal(hub, findings=["fin_1"])
    _accept(hub)
    chunk_id = _seed_chunk_answering_no_proposal(hub)

    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_1")
    assert resp.status_code == 404, resp.text
    assert "no accepted, minted garden proposal" in resp.json()["detail"]
