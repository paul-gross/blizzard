"""The ``DELETE /chunks/{id}`` route over the HTTP surface (issue #364, Phase 2). Proves
the controller wires ``DeleteService`` correctly end to end: 202/404/409, the chunk gone
from every read, its open hub item(s) withdrawn in the same write, and both
``chunk-changed``/``queue-changed`` published — the response shape and the degraded
``chunk-changed`` frame the delete route is the first (and only) caller to exercise."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.events.broker import CHUNK_CHANGED, QUEUE_CHANGED
from tests.support import build_hub, emitted_events, ingest, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "364"}


def _claim(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def _delete_chunk(hub, chunk_id: str, *, by: str | None = None):  # type: ignore[no-untyped-def]
    """``DELETE /api/chunks/{id}`` with its (issue #364) JSON body — ``httpx``'s own
    ``delete()`` refuses a ``json`` keyword, so this goes through ``request`` instead,
    the way ``CliContext.send`` itself now has to for the same reason."""
    body = {"by": by} if by is not None else {}
    return hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json=body)


def test_delete_returns_202_and_the_chunk_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = _delete_chunk(hub, chunk_id)

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"chunk_id": chunk_id}


def test_deleted_chunk_is_gone_from_every_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    assert _delete_chunk(hub, chunk_id).status_code == 202

    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 404
    ids = [c["chunk_id"] for c in hub.client.get("/api/chunks").json()]
    assert chunk_id not in ids


def test_delete_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = _delete_chunk(hub, "ch_nope")
    assert resp.status_code == 404


def test_delete_refuses_a_claimed_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)

    resp = _delete_chunk(hub, chunk_id)

    assert resp.status_code == 409, resp.text
    assert "running" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").status_code == 200


def test_delete_withdraws_the_chunks_open_hub_item(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/work-sources/hub/items", json={"title": "t", "body": "b"}).json()
    ref, chunk_id = created["ref"], created["chunk_id"]

    resp = _delete_chunk(hub, chunk_id)

    assert resp.status_code == 202, resp.text
    item = hub.client.get(f"/api/work-sources/hub/items/{ref}").json()
    assert item["closure"] == "withdrawn"


def test_delete_defaults_by_to_operator_and_accepts_an_override(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    default_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()
    assert _delete_chunk(hub, default_id).status_code == 202
    default_frame = json.loads(emitted_events(hub, since=since)[0]["data"])
    assert default_frame["by"] == "operator"

    named_id = ingest(hub, [{"source": "default", "ref": "364b"}])
    since = hub.events.latest_id()
    resp = _delete_chunk(hub, named_id, by="alice")
    assert resp.status_code == 202, resp.text
    named_frame = json.loads(emitted_events(hub, since=since)[0]["data"])
    assert named_frame["by"] == "alice"


def test_delete_publishes_both_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()

    resp = _delete_chunk(hub, chunk_id)

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert CHUNK_CHANGED in types
    assert QUEUE_CHANGED in types


def test_delete_chunk_changed_frame_carries_cause_deleted_and_by_no_richer_shape(tmp_path: Path) -> None:
    """The delete route is the only caller reaching ``ChunkChanged.publish``'s degraded
    branch (assumption 3): the chunk reads back ``None`` post-delete, so the frame is the
    bare ``{chunk_id, status, ...}`` shape — no ``node``/``runner_id``/``graph_id``."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()

    resp = _delete_chunk(hub, chunk_id, by="alice")
    assert resp.status_code == 202, resp.text

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["chunk_id"] == chunk_id
    assert frame["cause"] == "deleted"
    assert frame["by"] == "alice"
    assert frame["key"].startswith("chunk_deleted:")
    assert "graph_id" not in frame
    assert "node" not in frame
    assert "runner_id" not in frame


def test_delete_chunk_changed_frame_carries_the_chunks_pre_delete_status(tmp_path: Path) -> None:
    """The frame's status is the chunk's own status right before the delete — not the
    post-delete ``not_ready`` ``ChunkChanged.publish`` would otherwise always derive
    once the chunk's facts read back empty, regardless of what the chunk really was."""
    hub = build_hub(tmp_path)
    ready_id = ingest(hub, [_POINTER])  # promote=True by default -> ready
    since = hub.events.latest_id()

    resp = _delete_chunk(hub, ready_id)
    assert resp.status_code == 202, resp.text

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    assert frames[0]["status"] == "ready"

    not_ready_id = ingest(hub, [{"source": "default", "ref": "364c"}], promote=False)
    since = hub.events.latest_id()

    resp = _delete_chunk(hub, not_ready_id)
    assert resp.status_code == 202, resp.text

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    assert frames[0]["status"] == "not_ready"


def test_every_other_mutating_chunk_route_frame_is_unchanged_by_the_widened_degraded_branch(
    tmp_path: Path,
) -> None:
    """Widening the degraded branch (D7) to forward ``cause``/``prev_status``/``by``
    changes nothing for a route whose chunk still reads back — a stop's frame still
    carries the same enriched shape it always has."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
    assert resp.status_code == 202, resp.text

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["cause"] == "stopped"
    assert frame["status"] == "stopped"
    assert "by" not in frame
    assert "graph_id" in frame
