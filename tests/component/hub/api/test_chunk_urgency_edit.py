"""A chunk's urgency, edited and read (component tier): ``PATCH /api/chunks/{id}`` sets the urgency by id or slug,
leaves the chunk's place in its list where it was, writes nothing for the urgency it already carries, and refuses an
explicit ``null``, an unknown urgency, and a retired one. Every chunk view names the urgency by id and slug, and
grouping keeps the survivor's own."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.store import schema as s
from tests.support.scaffolding import HubHarness, build_hub, default_urgency, emitted_events, hub_scoped, ingest


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _urgency(hub: HubHarness, slug: str) -> str:
    resp = hub.client.post("/api/urgencies", json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["urgency_id"]


def _carried(hub: HubHarness, chunk_id: str) -> str | None:
    with hub.engine.connect() as conn:
        return conn.execute(
            hub_scoped(sa.select(s.chunks.c.urgency_id).where(s.chunks.c.chunk_id == chunk_id))
        ).scalar_one()


def _chunk_row(hub: HubHarness, chunk_id: str) -> dict[str, object]:
    with hub.engine.connect() as conn:
        row = conn.execute(hub_scoped(sa.select(s.chunks).where(s.chunks.c.chunk_id == chunk_id))).mappings().one()
    return dict(row)


def _queue(hub: HubHarness) -> list[tuple[str, int]]:
    return [(e["chunk_id"], e["position"]) for e in hub.client.get("/api/queue").json()["entries"]]


def test_an_edit_sets_the_urgency_and_every_view_names_it(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "urgent"})

    assert resp.status_code == 202, resp.text
    named = {"urgency_id": urgent, "slug": "urgent"}
    assert resp.json()["urgency"] == named
    assert _carried(hub, chunk_id) == urgent
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["urgency"] == named
    (summary,) = [c for c in hub.client.get("/api/chunks").json()["chunks"] if c["chunk_id"] == chunk_id]
    assert summary["urgency"] == named
    (entry,) = hub.client.get("/api/queue").json()["entries"]
    assert entry["urgency"] == named


def test_a_backlog_entry_names_the_urgency_a_mint_resolved(hub: HubHarness) -> None:
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)

    (entry,) = hub.client.get("/api/backlog").json()["entries"]

    assert entry["chunk_id"] == chunk_id
    assert entry["urgency"] == {"urgency_id": default_urgency(hub.engine), "slug": "standard"}


def test_an_edit_by_id_leaves_the_chunk_where_it_stands_in_the_queue(hub: HubHarness) -> None:
    urgent = _urgency(hub, "urgent")
    first = ingest(hub, [{"source": "default", "ref": "1"}])
    second = ingest(hub, [{"source": "default", "ref": "2"}])
    third = ingest(hub, [{"source": "default", "ref": "3"}])
    before = _queue(hub)
    rank_before = _chunk_row(hub, second)

    resp = hub.client.patch(f"/api/chunks/{second}", json={"urgency": urgent})

    assert resp.status_code == 202, resp.text
    assert _queue(hub) == before == [(first, 0), (second, 1), (third, 2)]
    after = _chunk_row(hub, second)
    assert {k: v for k, v in after.items() if k != "urgency_id"} == {
        k: v for k, v in rank_before.items() if k != "urgency_id"
    }
    assert after["urgency_id"] == urgent


def test_an_edit_publishes_the_queue_change_after_the_chunk_change(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    since = hub.events.latest_id()

    assert hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "urgent"}).status_code == 202

    assert [e["event"] for e in emitted_events(hub, since=since)] == ["chunk-changed", "queue-changed"]


def test_an_edit_to_the_urgency_the_chunk_carries_writes_nothing(hub: HubHarness) -> None:
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    before = _chunk_row(hub, chunk_id)
    since = hub.events.latest_id()

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "standard"})

    assert resp.status_code == 202, resp.text
    assert _chunk_row(hub, chunk_id) == before
    assert "queue-changed" not in [e["event"] for e in emitted_events(hub, since=since)]


def test_an_explicit_null_urgency_is_refused_and_writes_nothing(hub: HubHarness) -> None:
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    before = _chunk_row(hub, chunk_id)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": None, "default_effort": "high"})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", "urgency"]
    assert _chunk_row(hub, chunk_id) == before


def test_an_unknown_urgency_is_refused_and_writes_nothing(hub: HubHarness) -> None:
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    before = _chunk_row(hub, chunk_id)

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "nosuch"})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["type"] == "urgency_unknown"
    assert _chunk_row(hub, chunk_id) == before


def test_a_retired_urgency_is_refused_and_writes_nothing(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    assert hub.client.post("/api/urgencies/urgent/retire", json={}).status_code == 200
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "urgent"})

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["type"] == "urgency_retired"
    assert _carried(hub, chunk_id) == default_urgency(hub.engine)


def test_a_finished_chunk_refuses_an_urgency_edit(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    resp = hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "urgent"})

    assert resp.status_code == 409, resp.text
    assert _carried(hub, chunk_id) == default_urgency(hub.engine)


def test_grouping_keeps_the_survivors_urgency(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    survivor = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    folded = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)
    assert hub.client.patch(f"/api/chunks/{folded}", json={"urgency": "urgent"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [folded]})

    assert resp.status_code == 200, resp.text
    assert _carried(hub, survivor) == default_urgency(hub.engine)
    assert hub.client.get(f"/api/chunks/{survivor}").json()["urgency"]["slug"] == "standard"


def test_a_retire_moves_a_chunk_an_edit_set_to_it(hub: HubHarness) -> None:
    _urgency(hub, "urgent")
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    assert hub.client.patch(f"/api/chunks/{chunk_id}", json={"urgency": "urgent"}).status_code == 202

    resp = hub.client.post("/api/urgencies/urgent/retire", json={"move_to": "standard"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["moved_chunks"] == 1
    assert _carried(hub, chunk_id) == default_urgency(hub.engine)
