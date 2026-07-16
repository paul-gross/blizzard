"""PM pass-through read (D-047/D-084) — body + comments per pointer, never stored (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.pm.source import PmItem
from tests.support import FakePmSource, build_hub, pointer_token

pytestmark = pytest.mark.component

_POINTER = {"source": "widget", "ref": "42"}
_POINTER_2 = {"source": "widget", "ref": "43"}


def test_pm_items_reads_body_and_comments_from_the_forge(tmp_path: Path) -> None:
    pm = FakePmSource(name="widget", body="please fix the flake", comments=["seen it too", "repro attached"])
    hub = build_hub(tmp_path, pm={"widget": pm})
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/pm-items")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "widget"
    assert item["ref"] == "42"
    assert item["label"] == "widget#42"
    assert item["web_url"]
    assert item["body"] == "please fix the flake"
    assert item["comments"] == ["seen it too", "repro attached"]
    assert item["error"] is None
    assert item["fetched_at"]
    # The read went to the forge for this pointer — contents are fetched, not stored.
    assert pm.fetched == ["42"]


def test_pm_items_returns_one_entry_per_pointer(tmp_path: Path) -> None:
    """A grouped chunk carrying many pointers (D-047) yields one entry per pointer, order preserved."""
    pm = FakePmSource(
        name="widget",
        by_ref={
            "42": PmItem(body="first issue", comments=["a"]),
            "43": PmItem(body="second issue", comments=[]),
        },
    )
    hub = build_hub(tmp_path, pm={"widget": pm})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER), pointer_token(_POINTER_2)]}
    ).json()["chunk_id"]

    items = hub.client.get(f"/api/chunks/{chunk_id}/pm-items").json()["items"]
    assert [i["ref"] for i in items] == ["42", "43"]
    assert [i["body"] for i in items] == ["first issue", "second issue"]


def test_pm_items_degrades_per_pointer_when_the_forge_is_unreachable(tmp_path: Path) -> None:
    """One unreachable pointer surfaces as an ``error`` entry; the reachable one still reads (D-084)."""
    pm = FakePmSource(
        name="widget",
        by_ref={"42": PmItem(body="reachable", comments=[])},
        fail_refs={"43"},
    )
    hub = build_hub(tmp_path, pm={"widget": pm})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER), pointer_token(_POINTER_2)]}
    ).json()["chunk_id"]

    resp = hub.client.get(f"/api/chunks/{chunk_id}/pm-items")
    assert resp.status_code == 200
    ok, failed = resp.json()["items"]
    assert ok["body"] == "reachable" and ok["error"] is None
    assert failed["body"] is None and failed["error"] and "43" in failed["error"]


def test_pm_items_with_no_pointers_is_an_empty_list(tmp_path: Path) -> None:
    """A chunk with no pointers is the board's empty state — an empty list, 200, not a 404."""
    # Ingest guards against empty pointers at the front door (422), so mint the degenerate
    # empty-pointer chunk through the ingest service directly to prove the route still answers.
    hub = build_hub(tmp_path)
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    chunk_id = hub.services.ingest.ingest([], graph=graph)

    resp = hub.client.get(f"/api/chunks/{chunk_id}/pm-items")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_pm_items_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/chunks/ch_missing/pm-items").status_code == 404
