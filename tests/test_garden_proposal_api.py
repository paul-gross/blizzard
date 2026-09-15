"""Garden-proposal routes — the read half (blizzard#390, component tier).

Seeded straight through ``GardenProposalStore`` since no route writes a proposal yet
(passing/accepting is a sibling issue), the ``tests/test_finding_api.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import build_hub, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed(hub) -> None:  # type: ignore[no-untyped-def]
    with hub.engine.begin() as conn:
        conn.execute(insert(s.scopes).values(slug="blizzard", description="", created_at=_NOW))
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


def test_list_renders_every_proposal_newest_first(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    proposals = GardenProposalStore(hub_store_connections(hub.engine))
    proposals.create(
        "gprop_old", routine_name="nightly", class_="c", title="old", body="b", findings=["fin_1"], at=_NOW
    )
    proposals.create(
        "gprop_new",
        routine_name="nightly",
        class_="c",
        title="new",
        body="b",
        findings=["fin_1"],
        at=_NOW.replace(hour=13),
    )

    resp = hub.client.get("/api/garden-proposals")

    assert resp.status_code == 200, resp.text
    assert [row["proposal_id"] for row in resp.json()["proposals"]] == ["gprop_new", "gprop_old"]


def test_get_renders_one_proposal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_1",
        routine_name="nightly",
        class_="fix-the-source",
        title="Author a docstring standard",
        body="the case",
        findings=["fin_1"],
        at=_NOW,
    )

    resp = hub.client.get("/api/garden-proposals/gprop_1")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["class"] == "fix-the-source"
    assert body["findings"] == ["fin_1"]


def test_get_unknown_id_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/garden-proposals/gprop_ghost")

    assert resp.status_code == 404, resp.text


# --- paging: sort order and the limit/cursor contract (blizzard#526) --------------


def test_paged_concatenation_matches_the_full_order_through_a_created_at_tie(tmp_path: Path) -> None:
    """`gprop_a`/`gprop_b` share the same `created_at`; only the `proposal_id` tiebreak
    (`desc`) makes the order total. Paging one row at a time must retrace the same
    order a single unpaginated read renders."""
    hub = build_hub(tmp_path)
    _seed(hub)
    proposals = GardenProposalStore(hub_store_connections(hub.engine))
    proposals.create("gprop_a", routine_name="nightly", class_="c", title="a", body="b", findings=["fin_1"], at=_NOW)
    proposals.create("gprop_b", routine_name="nightly", class_="c", title="b", body="b", findings=["fin_1"], at=_NOW)
    proposals.create(
        "gprop_c", routine_name="nightly", class_="c", title="c", body="b", findings=["fin_1"], at=_NOW.replace(hour=13)
    )
    proposals.create(
        "gprop_d", routine_name="nightly", class_="c", title="d", body="b", findings=["fin_1"], at=_NOW.replace(hour=11)
    )

    full = hub.client.get("/api/garden-proposals")
    assert full.status_code == 200, full.text
    full_order = [row["proposal_id"] for row in full.json()["proposals"]]
    assert full_order == ["gprop_c", "gprop_b", "gprop_a", "gprop_d"]

    paged_order: list[str] = []
    cursor: str | None = None
    for _ in range(len(full_order) + 1):
        params = {"limit": 1} if cursor is None else {"limit": 1, "cursor": cursor}
        resp = hub.client.get("/api/garden-proposals", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["proposals"]) == 1
        paged_order.append(body["proposals"][0]["proposal_id"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    else:
        pytest.fail("paging through by limit=1 never reached a null next_cursor")

    assert paged_order == full_order


def test_list_422s_on_limit_over_the_ceiling(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/garden-proposals", params={"limit": 1001})

    assert resp.status_code == 422, resp.text


def test_list_422s_on_limit_under_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/garden-proposals", params={"limit": 0})

    assert resp.status_code == 422, resp.text


def test_list_422s_on_a_malformed_cursor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.get("/api/garden-proposals", params={"cursor": "not-valid-base64!!!"})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "malformed cursor"


def test_list_next_cursor_is_null_only_on_the_last_page(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _seed(hub)
    proposals = GardenProposalStore(hub_store_connections(hub.engine))
    proposals.create("gprop_1", routine_name="nightly", class_="c", title="1", body="b", findings=["fin_1"], at=_NOW)
    proposals.create(
        "gprop_2", routine_name="nightly", class_="c", title="2", body="b", findings=["fin_1"], at=_NOW.replace(hour=13)
    )
    proposals.create(
        "gprop_3", routine_name="nightly", class_="c", title="3", body="b", findings=["fin_1"], at=_NOW.replace(hour=14)
    )

    first = hub.client.get("/api/garden-proposals", params={"limit": 2})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["proposals"]) == 2
    assert first_body["next_cursor"] is not None

    second = hub.client.get("/api/garden-proposals", params={"limit": 2, "cursor": first_body["next_cursor"]})
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["proposals"]) == 1
    assert second_body["next_cursor"] is None
