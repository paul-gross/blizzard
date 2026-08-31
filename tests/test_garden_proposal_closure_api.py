"""Garden-proposal closure routes — pass and accept (blizzard#395, component tier).

Seeded straight through ``GardenProposalStore``/``FindingStore``, the
``tests/test_garden_proposal_api.py`` shape. The ``tests/test_hub_work_source_api.py``
shape for accept's own mint-and-publish behavior, since accepting rides the same
mint-a-chunk machinery a hub item's own creation does."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.findings import IReadFindingRepository
from blizzard.hub.events.broker import CHUNK_CHANGED, QUEUE_CHANGED
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import HubHarness, build_hub, emitted_events, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed(hub: HubHarness, *, proposal_id: str = "gprop_1", body: str = "the case") -> None:
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
        proposal_id,
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body=body,
        findings=["fin_1"],
        at=_NOW,
    )


# --------------------------------------------------------------------------- #
# POST /garden-proposals/{id}/pass


def test_pass_records_the_reason_and_renders_on_a_later_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "not worth it"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["closure"]["closure"] == "passed"
    assert body["closure"]["reason"] == "not worth it"
    assert body["closure"]["item_outcome"] is None

    fetched = hub.client.get("/api/garden-proposals/gprop_1").json()
    assert fetched["closure"]["closure"] == "passed"


def test_pass_with_a_blank_reason_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "   "})

    assert resp.status_code == 422, resp.text
    assert hub.client.get("/api/garden-proposals/gprop_1").json()["closure"] is None


def test_pass_an_unknown_proposal_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/garden-proposals/gprop_ghost/pass", json={"reason": "r"})

    assert resp.status_code == 404, resp.text


def test_a_second_pass_is_409_naming_the_existing_closure(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "first"})

    resp = hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "second"})

    assert resp.status_code == 409, resp.text
    assert "passed" in resp.text
    fetched = hub.client.get("/api/garden-proposals/gprop_1").json()
    assert fetched["closure"]["reason"] == "first"  # unchanged


# --------------------------------------------------------------------------- #
# POST /garden-proposals/{id}/accept


def test_accept_with_no_body_override_mints_carrying_the_proposals_body(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub, body="the proposal's own body")

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunk_id"] is not None
    assert body["closure"]["closure"] == "accepted"
    assert body["closure"]["item_outcome"] == "minted"
    source, ref = body["closure"]["source"], body["closure"]["ref"]
    assert source == "hub"

    item = hub.client.get(f"/api/work-sources/{source}/items/{ref}").json()
    assert item["body"] == "the proposal's own body"
    assert item["closure"] is None  # the item itself is open

    chunk = hub.client.get(f"/api/chunks/{body['chunk_id']}").json()
    assert chunk["status"] == "not_ready"  # rests behind the ordinary promote gate


def test_accept_with_a_body_override_mints_with_that_body(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub, body="the proposal's own body")

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"body": "a hand-drafted body"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    source, ref = body["closure"]["source"], body["closure"]["ref"]
    item = hub.client.get(f"/api/work-sources/{source}/items/{ref}").json()
    assert item["body"] == "a hand-drafted body"


def test_accept_declining_to_mint_records_declined_and_no_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post(
        "/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False, "reason": "handled by hand"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunk_id"] is None
    assert body["closure"]["closure"] == "accepted"
    assert body["closure"]["item_outcome"] == "declined"
    assert body["closure"]["reason"] == "handled by hand"
    assert body["closure"]["source"] is None and body["closure"]["ref"] is None
    assert hub.client.get("/api/chunks").json() == []  # nothing minted


def test_accept_promotes_nothing_and_leaves_the_finding_untouched(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    findings: IReadFindingRepository = hub.services.findings
    before = findings.get("fin_1")
    assert before is not None

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})

    assert resp.status_code == 200, resp.text
    chunk = hub.client.get(f"/api/chunks/{resp.json()['chunk_id']}").json()
    assert chunk["status"] == "not_ready"  # never promoted
    after = findings.get("fin_1")
    assert after is not None
    assert after.live == before.live
    assert after.last_seen_at == before.last_seen_at
    assert after.observed_count == before.observed_count


def test_accept_publishes_the_mints_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})

    assert resp.status_code == 200, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert [e["event"] for e in emitted_events(hub)] == [CHUNK_CHANGED, QUEUE_CHANGED]
    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == CHUNK_CHANGED]
    assert frames[0]["chunk_id"] == chunk_id
    assert frames[0]["cause"] == "minted"


def test_accept_declining_to_mint_publishes_no_events(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={"mint_work_item": False})

    assert resp.status_code == 200, resp.text
    assert emitted_events(hub) == []


def test_accept_an_unknown_proposal_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/garden-proposals/gprop_ghost/accept", json={})

    assert resp.status_code == 404, resp.text


def test_a_second_accept_is_409_naming_the_existing_closure_and_mints_nothing_more(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    first = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})
    assert first.status_code == 200, first.text

    second = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})

    assert second.status_code == 409, second.text
    assert len(hub.client.get("/api/chunks").json()) == 1  # nothing minted a second time


def test_accept_after_a_pass_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "r"})

    resp = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})

    assert resp.status_code == 409, resp.text
    assert hub.client.get("/api/chunks").json() == []
