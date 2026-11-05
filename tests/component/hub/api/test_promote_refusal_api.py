"""``POST /chunks/{id}/promote`` on a terminal chunk (component tier): a never-promoted chunk that
was stopped or completed is refused with 409 and gains no promotion or queue position; an
already-promoted one replays as a 202 that writes nothing."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.support.scaffolding import build_hub, chunk_stores, ingest, ranking_unit

_POINTER = {"source": "default", "ref": "41"}


@pytest.mark.parametrize("verb", ["stop", "complete"])
def test_promote_refuses_a_never_promoted_terminal_chunk(tmp_path: Path, verb: str) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    assert hub.client.post(f"/api/chunks/{chunk_id}/{verb}", json={"by": "alice"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote")

    assert resp.status_code == 409
    expected = "stopped" if verb == "stop" else "done"
    assert resp.json()["detail"] == f"chunk {chunk_id} is {expected}, not promotable"
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None and facts.promoted is False
    assert hub.services.chunks.queue.queue_positions([chunk_id]) == {}


def test_promote_replays_on_an_already_promoted_terminal_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=True)
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote")

    assert resp.status_code == 202
    assert resp.json()["status"] == "stopped"


def test_a_promote_refused_after_a_spent_gap_leaves_the_ready_positions_unchanged(tmp_path: Path) -> None:
    """A stopped chunk's promote naming an anchor whose gap to its neighbour is spent is refused,
    and every ready position stays as it was stored."""
    hub = build_hub(tmp_path)
    a = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    b = ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"}).status_code == 202
    stores = chunk_stores(hub.engine, hub.clock)
    with ranking_unit(hub.engine) as unit:
        stores.queue.record_queue_positions(
            [(a, 1.0), (b, math.nextafter(1.0, math.inf))], at=hub.clock.now(), transaction=unit
        )
    before = stores.queue.queue_positions([a, b])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/promote", json={"after_chunk_id": a})

    assert resp.status_code == 409, resp.text
    assert stores.queue.queue_positions([a, b, chunk_id]) == before
