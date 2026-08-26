"""The ``/chunks/{id}/complete`` route over the HTTP surface (issue #294).

Proves the controller wires an operator completion correctly end to end: 202/404, the
fact written, the route + hub-exec slot released, the events published, that completion
is reachable from ``stopped``, and that it is idempotent by no-op on an already-``done``
chunk rather than an error."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select

from blizzard.hub.domain.work import IWriteChunkRepository
from blizzard.hub.store import schema as s
from tests.support import assert_all_timestamps_utc, build_hub, emitted_events, ingest, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}


def _writable(hub) -> IWriteChunkRepository:  # type: ignore[no-untyped-def]
    """A test-only cast — see ``test_hub_command_node.py``'s helper of the same name."""
    return cast(IWriteChunkRepository, hub.services.chunks)


_MERGE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _claim(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def _ingest_and_deliver(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _MERGE_YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    build_node_id = detail["current_node_id"]
    apply = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [{"command": "mise run test", "passed": True}],
            "artifacts": [
                {
                    "name": "work",
                    "kind": "git_commit",
                    "repo": "acme/widget",
                    "branch_name": "blizzard/ch-12",
                    "commit_hash": "abc123",
                }
            ],
        },
    )
    assert apply.status_code == 200, apply.text
    return chunk_id


def test_complete_returns_202_writes_a_fact_and_the_chunk_derives_done(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "alice"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["chunk_id"] == chunk_id
    assert body["status"] == "done"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert_all_timestamps_utc(detail)


def test_complete_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_complete_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_nope/complete", json={"by": "operator"})
    assert resp.status_code == 404


def test_complete_is_reachable_from_stopped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_double_complete_is_a_no_op_not_an_error(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_and_deliver(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_completing_an_already_done_chunk_writes_no_second_fact(tmp_path: Path) -> None:
    """Idempotent in the table, not only in the response: after a second ``complete``,
    ``chunk_completed`` holds exactly one row for the chunk — a guard that inserted a
    second row while still answering 202/``done`` would pass every derived-status read."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    assert hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"}).status_code == 202
    assert hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"}).status_code == 202

    with hub.engine.connect() as conn:
        count = conn.execute(
            select(func.count()).select_from(s.chunk_completed).where(s.chunk_completed.c.chunk_id == chunk_id)
        ).scalar_one()
    assert count == 1


def test_complete_while_running_releases_the_live_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is not None

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert detail["route"] is None, "complete releases the live route in the same operation"


def test_complete_releases_a_held_fleet_wide_hub_exec_slot(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    chunks = _writable(hub)
    slot_id = chunks.acquire_hub_exec_slot(
        chunk_id, node_id="nd_deliver", at=hub.clock.now(), stale_after=timedelta(minutes=5)
    )
    assert slot_id is not None
    assert chunks.count_live_hub_exec_slots() == 1

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert chunks.count_live_hub_exec_slots() == 0


def test_complete_publishes_both_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
    assert "queue-changed" in types
    assert any(chunk_id in e["data"] and '"status": "done"' in e["data"] for e in events)


def _peek_ids(hub) -> list[str]:  # type: ignore[no-untyped-def]
    resp = hub.client.get("/api/queue")
    assert resp.status_code == 200, resp.text
    return [e["chunk_id"] for e in resp.json()["entries"]]


def test_completed_chunk_is_excluded_from_the_ready_queue(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = ingest(hub, [_POINTER])
    b = ingest(hub, [{"source": "default", "ref": "13"}])

    assert hub.client.post(f"/api/chunks/{a}/complete", json={"by": "operator"}).status_code == 202

    assert _peek_ids(hub) == [b]
