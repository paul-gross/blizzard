"""The ``/chunks/{id}/stop`` route over the HTTP surface.

Proves the controller wires stop correctly end to end: 202/404/409, the fact written,
the route released, waiting flow submissions withdrawn, the events published, and that a stopped chunk
never re-enters the ready queue."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.delivery.flows import FlowResource
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support import (
    assert_all_timestamps_utc,
    build_hub,
    emitted_events,
    hub_store_connections,
    ingest,
    report_lease,
)

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}


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
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _claim(hub, chunk_id: str) -> None:
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)


def _ask(hub, chunk_id: str, *, question_id: str = "qn_1") -> None:
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": question_id,
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which API?",
            "options": ["rest", "graphql"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert resp.status_code == 201, resp.text


def _ingest_and_deliver(hub) -> str:
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
                    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                }
            ],
        },
    )
    assert apply.status_code == 200, apply.text
    hub.settle_turns()
    return chunk_id


def test_stop_returns_202_writes_a_fact_and_the_chunk_derives_stopped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["chunk_id"] == chunk_id
    assert body["status"] == "stopped"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert_all_timestamps_utc(detail)


def test_stop_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"


def test_stop_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_nope/stop", json={"by": "operator"})
    assert resp.status_code == 404


def test_stop_refuses_a_done_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_and_deliver(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 409, resp.text
    assert "done" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_double_stop_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 409, resp.text
    assert "stopped" in resp.json()["detail"]


def test_stop_while_running_releases_the_live_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is not None

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert detail["route"] is None, "stop releases the live route in the same operation"


def test_stop_while_waiting_on_human_with_a_parked_worker_releases_the_route(tmp_path: Path) -> None:
    """The chunk holds a live route from a worker parked on an open question — stop
    still releases it in the same operation, same as the plain-running case."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _claim(hub, chunk_id)
    _ask(hub, chunk_id)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "waiting_on_human"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert detail["route"] is None


def test_stop_with_no_live_route_still_succeeds(tmp_path: Path) -> None:
    """Stop never requires a live route to release."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"


def test_stop_publishes_both_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
    assert "queue-changed" in types
    assert any(chunk_id in e["data"] and '"status": "stopped"' in e["data"] for e in events)


def _peek_ids(hub) -> list[str]:
    resp = hub.client.get("/api/queue")
    assert resp.status_code == 200, resp.text
    return [e["chunk_id"] for e in resp.json()["entries"]]


def test_stopped_chunk_is_excluded_from_the_ready_queue(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    a = ingest(hub, [_POINTER])
    b = ingest(hub, [{"source": "default", "ref": "13"}])
    hub.clock.advance(timedelta(seconds=1))

    assert hub.client.post(f"/api/chunks/{a}/stop", json={"by": "operator"}).status_code == 202

    assert _peek_ids(hub) == [b]


def test_stopped_chunk_with_a_stale_live_route_is_still_excluded_from_the_queue(tmp_path: Path) -> None:
    """Even setting aside that stop itself releases the route, stopped outranks ready
    unconditionally in the derivation — pinned here as a property, mirroring
    ``test_paused_chunk_with_a_live_route_is_still_excluded_from_the_queue``."""
    hub = build_hub(tmp_path)
    a = ingest(hub, [_POINTER])
    _claim(hub, a)

    assert hub.client.post(f"/api/chunks/{a}/stop", json={"by": "operator"}).status_code == 202

    assert _peek_ids(hub) == []


def test_stop_withdraws_a_waiting_submission_and_leaves_a_mid_turn_one_to_its_turn(tmp_path: Path) -> None:
    """The withdrawal rides the same atomic write as the terminal fact; a submission whose turn is open keeps its
    place until that turn's end, so no branch is freed while a pass may still be landing on it."""
    hub = build_hub(tmp_path)
    waiting_chunk = ingest(hub, [_POINTER])
    turning_chunk = ingest(hub, [{"source": "default", "ref": "13"}])
    flows = FlowStore(hub_store_connections(hub.engine), hub.clock)
    now = hub.clock.now()
    waiting = flows.submit(
        waiting_chunk, node_id="nd_deliver", epoch=1, generation=0, resources=[FlowResource("re_a", "main")], at=now
    )
    turning = flows.submit(
        turning_chunk, node_id="nd_deliver", epoch=1, generation=0, resources=[FlowResource("re_b", "main")], at=now
    )
    assert waiting is not None and turning is not None
    assert flows.take_turn(turning.submission_id, passed_over=frozenset(), terms=(), at=now) is not None

    for chunk_id in (waiting_chunk, turning_chunk):
        resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
        assert resp.status_code == 202, resp.text

    withdrawn = flows.submission_for_visit(waiting_chunk, node_id="nd_deliver", epoch=1)
    assert withdrawn is not None and withdrawn.closed == "stopped"
    assert [(f.kind, f.outcome) for f in flows.facts_of(waiting.submission_id)][-1] == ("withdrawn", "stopped")
    mid_turn = flows.submission_for_visit(turning_chunk, node_id="nd_deliver", epoch=1)
    assert mid_turn is not None and mid_turn.closed is None
