"""The urgency and tenant-settings HTTP surfaces over a tenant-scoped hub: the verbs, the set's two reorders, the
icon, the retire moving open work, and the settings edit."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from blizzard.auth_core import Role
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.urgency_chunks_store import UrgencyChunksStore
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    count_queries,
    hub_store_connections,
    ingest,
    seed_session,
    seed_user,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _create(hub: HubHarness, slug: str, **fields: Any) -> dict[str, Any]:
    response = hub.client.post("/api/urgencies", json={"slug": slug, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def _slugs(hub: HubHarness, **params: Any) -> list[str]:
    response = hub.client.get("/api/urgencies", params=params)
    assert response.status_code == 200, response.text
    return [u["slug"] for u in response.json()["urgencies"]]


def _show(hub: HubHarness, key: str) -> dict[str, Any]:
    response = hub.client.get(f"/api/urgencies/{key}")
    assert response.status_code == 200, response.text
    return response.json()


def _changes(hub: HubHarness, kind: str) -> list[dict[str, Any]]:
    response = hub.client.get("/api/config/changes", params={"record_kind": kind, "limit": 100})
    assert response.status_code == 200, response.text
    return response.json()["changes"]


def _carry(hub: HubHarness, urgency_id: str, *chunk_ids: str) -> None:
    """Set each chunk to carry ``urgency_id`` whatever its status — a finished one included, which a move leaves."""
    store = hub_store_connections(hub.engine)
    with store.write("carry_urgency") as conn:
        conn.execute(store.update(s.chunks).where(s.chunks.c.chunk_id.in_(chunk_ids)).values(urgency_id=urgency_id))


def _carried(hub: HubHarness, chunk_id: str) -> str:
    store = hub_store_connections(hub.engine)
    with store.read("carried_urgency") as conn:
        return conn.execute(
            store.select(s.chunks, s.chunks.c.urgency_id).where(s.chunks.c.chunk_id == chunk_id)
        ).scalar_one()


def _field(response: Any) -> tuple[int, list[str], str]:
    entry = response.json()["detail"][0]
    return response.status_code, entry["loc"], entry["type"]


def test_a_tenant_starts_with_standard_as_its_default(hub: HubHarness) -> None:
    (standard,) = hub.client.get("/api/urgencies").json()["urgencies"]
    assert (standard["slug"], standard["default"], standard["place"], standard["open_chunks"]) == (
        "standard",
        True,
        1,
        0,
    )
    settings = hub.client.get("/api/settings").json()
    assert settings["default_urgency"] == {"urgency_id": standard["urgency_id"], "slug": "standard"}


def test_a_create_lands_last_first_or_after_and_picks_an_unused_color(hub: HubHarness) -> None:
    _create(hub, "urgent", emoji="🔥", after=None)
    background = _create(hub, "background")
    _create(hub, "soon", after="urgent")
    assert _slugs(hub) == ["urgent", "soon", "standard", "background"]
    assert background["color"] != _show(hub, "standard")["color"]
    assert background["name"] == "background"
    assert background["revision"] == 1


def test_a_create_refuses_a_bad_mark_a_taken_slug_and_both_marks(hub: HubHarness) -> None:
    assert _field(hub.client.post("/api/urgencies", json={"slug": "bad", "emoji": "ab"})) == (
        422,
        ["body", "emoji"],
        "value_error",
    )
    assert _field(hub.client.post("/api/urgencies", json={"slug": "standard"}))[2] == "urgency_slug_taken"
    both = {"slug": "both", "emoji": "🔥", "icon": {"media_type": "image/png", "data": base64.b64encode(_PNG).decode()}}
    assert _field(hub.client.post("/api/urgencies", json=both))[2] == "urgency_mark_conflict"
    assert _field(hub.client.post("/api/urgencies", json={"slug": "late", "after": "nope"}))[1] == ["body", "after"]
    assert hub.client.post("/api/urgencies", json={"slug": "x", "extra": 1}).status_code == 422


def test_an_edit_tells_null_from_absent_and_refuses_a_slug_and_a_stale_revision(hub: HubHarness) -> None:
    _create(hub, "urgent", emoji="🔥")
    cleared = hub.client.patch("/api/urgencies/urgent", json={"emoji": None, "name": "Urgent"})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["emoji"], cleared.json()["name"], cleared.json()["revision"]) == (None, "Urgent", 2)
    assert hub.client.patch("/api/urgencies/urgent", json={}).json()["revision"] == 2
    assert _field(hub.client.patch("/api/urgencies/urgent", json={"slug": "other"}))[1] == ["body", "slug"]
    assert _field(hub.client.patch("/api/urgencies/urgent", json={"color": None}))[1] == ["body", "color"]
    stale = hub.client.patch("/api/urgencies/urgent", json={"name": "U"}, headers={"If-Match": "1"})
    assert stale.status_code == 409
    assert hub.client.get("/api/urgencies/nope").status_code == 404


def test_the_icon_is_served_by_its_digest_under_a_policy_that_runs_nothing(hub: HubHarness) -> None:
    created = _create(hub, "pic", icon={"media_type": "image/png", "data": base64.b64encode(_PNG).decode()})
    assert created["icon"]["media_type"] == "image/png"
    served = hub.client.get("/api/urgencies/pic/icon")
    assert served.status_code == 200
    assert served.content == _PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["etag"] == f'"{created["icon"]["digest"]}"'
    assert served.headers["content-security-policy"] == "default-src 'none'"
    again = hub.client.get("/api/urgencies/pic/icon", headers={"If-None-Match": served.headers["etag"]})
    assert again.status_code == 304
    assert hub.client.get("/api/urgencies/standard/icon").status_code == 404


def test_a_retire_moves_open_work_and_finished_work_keeps_its_urgency(hub: HubHarness) -> None:
    urgent = _create(hub, "urgent")
    standard = _show(hub, "standard")
    open_ids = [ingest(hub, [{"source": "default", "ref": f"open-{n}"}]) for n in range(2)]
    done_id = ingest(hub, [{"source": "default", "ref": "done"}])
    assert hub.client.post(f"/api/chunks/{done_id}/complete", json={}).status_code == 202
    _carry(hub, urgent["urgency_id"], *open_ids, done_id)
    assert _show(hub, "urgent")["open_chunks"] == 2

    in_use = hub.client.post("/api/urgencies/urgent/retire", json={})
    assert in_use.status_code == 409
    assert (in_use.json()["detail"]["code"], in_use.json()["detail"]["open_chunks"]) == ("urgency_in_use", 2)
    assert _field(hub.client.post("/api/urgencies/urgent/retire", json={"move_to": "urgent"}))[2] == (
        "urgency_move_to_self"
    )
    assert _field(hub.client.post("/api/urgencies/urgent/retire", json={"move_to": "nope"}))[2] == "urgency_unknown"

    retired = hub.client.post("/api/urgencies/urgent/retire", json={"move_to": "standard"})
    assert retired.status_code == 200, retired.text
    assert (retired.json()["retired"], retired.json()["moved_chunks"], retired.json()["place"]) == (True, 2, None)
    assert _show(hub, "standard")["open_chunks"] == standard["open_chunks"] + 2
    assert _show(hub, "urgent")["open_chunks"] == 0
    assert _slugs(hub) == ["standard"]
    assert _slugs(hub, retired="true") == ["standard", "urgent"]
    (change,) = [c for c in _changes(hub, "urgency") if c["record_key"] == urgent["urgency_id"] and c["op"] == "retire"]
    diff = {d["field"]: d["new"] for d in change["diff"]}
    assert (diff["move_to"], diff["moved_chunks"]) == ("standard", 2)
    held = UrgencyChunksStore(hub_store_connections(hub.engine))
    assert held.open_chunks(UrgencyId(standard["urgency_id"])) == sorted(open_ids)
    again = hub.client.post("/api/urgencies/urgent/retire", json={})
    assert (again.status_code, again.json()["revision"]) == (200, retired.json()["revision"])


def test_a_retire_leaves_a_chunk_that_finished_after_its_read_ahead_and_counts_only_what_moved(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the unlocked read-ahead still sees the finished chunk; the re-read under the chunk rows' lock drops it."""
    urgent = _create(hub, "urgent")
    open_id = ingest(hub, [{"source": "default", "ref": "open"}])
    done_id = ingest(hub, [{"source": "default", "ref": "done"}])
    _carry(hub, urgent["urgency_id"], open_id, done_id)
    read = UrgencyChunksStore.open_chunks
    assert hub.client.post(f"/api/chunks/{done_id}/complete", json={}).status_code == 202

    def read_before_it_finished(self: UrgencyChunksStore, *args: Any, **kwargs: Any) -> list[str]:
        held = read(self, *args, **kwargs)
        return held if kwargs.get("transaction") is not None else sorted([*held, done_id])

    monkeypatch.setattr(UrgencyChunksStore, "open_chunks", read_before_it_finished)
    retired = hub.client.post("/api/urgencies/urgent/retire", json={"move_to": "standard"})

    assert retired.status_code == 200, retired.text
    assert retired.json()["moved_chunks"] == 1
    assert (_carried(hub, open_id), _carried(hub, done_id)) == (
        _show(hub, "standard")["urgency_id"],
        urgent["urgency_id"],
    )
    (change,) = [c for c in _changes(hub, "urgency") if c["record_key"] == urgent["urgency_id"] and c["op"] == "retire"]
    assert {d["field"]: d["new"] for d in change["diff"]}["moved_chunks"] == 1


def test_a_re_retire_with_a_stale_revision_is_refused_and_writes_nothing(hub: HubHarness) -> None:
    _create(hub, "urgent")
    retired = hub.client.post("/api/urgencies/urgent/retire", json={})
    assert retired.status_code == 200, retired.text
    changes = len(_changes(hub, "urgency"))
    stale = hub.client.post("/api/urgencies/urgent/retire", json={}, headers={"If-Match": "1"})
    assert stale.status_code == 409, stale.text
    assert _show(hub, "urgent")["revision"] == retired.json()["revision"]
    assert len(_changes(hub, "urgency")) == changes


def test_the_default_is_refused_retire_first(hub: HubHarness) -> None:
    _create(hub, "urgent")
    refused = hub.client.post("/api/urgencies/standard/retire", json={"move_to": "urgent"})
    assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "urgency_is_default")


def test_enable_returns_to_the_standing_place_and_moves_no_chunk_back(hub: HubHarness) -> None:
    urgent = _create(hub, "urgent", after=None)
    chunk_id = ingest(hub, [{"source": "default", "ref": "work"}])
    _carry(hub, urgent["urgency_id"], chunk_id)
    assert hub.client.post("/api/urgencies/urgent/retire", json={"move_to": "standard"}).status_code == 200
    enabled = hub.client.post("/api/urgencies/urgent/enable")
    assert enabled.status_code == 200, enabled.text
    assert (enabled.json()["retired"], enabled.json()["place"], enabled.json()["open_chunks"]) == (False, 1, 0)
    assert _slugs(hub) == ["urgent", "standard"]
    assert _show(hub, "standard")["open_chunks"] == 1


def test_a_settings_edit_moves_the_default_and_refuses_unknown_retired_and_null(hub: HubHarness) -> None:
    urgent = _create(hub, "urgent")
    _create(hub, "old")
    assert hub.client.post("/api/urgencies/old/retire", json={}).status_code == 200

    assert _field(hub.client.patch("/api/settings", json={"default_urgency": "nope"})) == (
        422,
        ["body", "default_urgency"],
        "urgency_unknown",
    )
    assert _field(hub.client.patch("/api/settings", json={"default_urgency": "old"}))[2] == "urgency_retired"
    assert _field(hub.client.patch("/api/settings", json={"default_urgency": None}))[1] == ["body", "default_urgency"]
    assert hub.client.get("/api/settings").json()["revision"] == 1

    edited = hub.client.patch("/api/settings", json={"default_urgency": urgent["urgency_id"]})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["default_urgency"]["slug"], edited.json()["revision"]) == ("urgent", 2)
    assert _show(hub, "urgent")["default"] is True
    stale = hub.client.patch("/api/settings", json={"default_urgency": "standard"}, headers={"If-Match": "1"})
    assert stale.status_code == 409
    (change,) = [c for c in _changes(hub, "tenant_settings") if c["op"] == "edit"]
    assert [(d["field"], d["old"], d["new"]) for d in change["diff"]] == [("default_urgency", "standard", "urgent")]


def test_each_reorder_appends_one_order_row_and_one_that_moves_nothing_appends_none(hub: HubHarness) -> None:
    _create(hub, "urgent")
    _create(hub, "background")
    before = len(_changes(hub, "urgency_order"))

    put = hub.client.put("/api/urgencies/order", json={"urgencies": ["background", "standard", "urgent"]})
    assert put.status_code == 200, put.text
    assert [u["slug"] for u in put.json()["urgencies"]] == ["background", "standard", "urgent"]
    assert len(_changes(hub, "urgency_order")) == before + 1

    moved = hub.client.post("/api/urgencies/order/position", json={"urgency": "urgent", "after": None})
    assert [u["slug"] for u in moved.json()["urgencies"]] == ["urgent", "background", "standard"]
    orders = _changes(hub, "urgency_order")
    assert len(orders) == before + 2
    assert [(d["field"], d["old"], d["new"]) for d in orders[0]["diff"]] == [
        ("order", ["background", "standard", "urgent"], ["urgent", "background", "standard"])
    ]

    assert hub.client.put("/api/urgencies/order", json={"urgencies": ["urgent", "background", "standard"]}).status_code
    assert len(_changes(hub, "urgency_order")) == before + 2
    incomplete = hub.client.put("/api/urgencies/order", json={"urgencies": ["standard"]})
    assert _field(incomplete)[2] == "urgency_order_incomplete"


def test_a_reorder_resolves_its_keys_in_statements_flat_in_how_many_it_names(tmp_path: Path) -> None:
    def statements(name: str, created: list[str]) -> int:
        (tmp_path / name).mkdir()
        tenant = build_hub(tmp_path / name)
        for slug in created:
            _create(tenant, slug)
        reordered = list(reversed(_slugs(tenant)))

        def put() -> None:
            response = tenant.client.put("/api/urgencies/order", json={"urgencies": reordered})
            assert response.status_code == 200, response.text

        return count_queries(tenant.engine, put)

    assert statements("two", ["urgent"]) == statements("five", ["urgent", "high", "low", "background"])


def test_reads_need_fleet_view_and_writes_config_edit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='ada', role=Role.ADMIN))}"}
    contributor = {
        "Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='con', role=Role.CONTRIBUTOR))}"
    }
    guest = {"Authorization": f"Bearer {seed_session(hub, seed_user(hub, username='gus', role=Role.GUEST))}"}
    for path in ("/api/urgencies", "/api/urgencies/standard", "/api/settings"):
        assert hub.client.get(path, headers=guest).status_code == 200
    assert hub.client.post("/api/urgencies", json={"slug": "u"}, headers=contributor).status_code == 403
    assert hub.client.patch("/api/settings", json={}, headers=contributor).status_code == 403
    assert hub.client.post("/api/urgencies", json={"slug": "u"}, headers=admin).status_code == 201
    assert hub.client.post("/api/urgencies/u/retire", json={}, headers=contributor).status_code == 403
    # The permission is checked before the key resolves, so an unknown key is 403 to a contributor, never 404.
    assert hub.client.patch("/api/urgencies/nope", json={"name": "x"}, headers=contributor).status_code == 403
    assert hub.client.post("/api/urgencies/nope/retire", json={}, headers=contributor).status_code == 403
    assert hub.client.post("/api/urgencies/nope/enable", headers=contributor).status_code == 403
    assert hub.client.patch("/api/urgencies/nope", json={"name": "x"}, headers=admin).status_code == 404
