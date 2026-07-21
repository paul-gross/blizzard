"""The ``/chunks/{id}/stop`` route over the HTTP surface (issue #118).

Stop is the operator's terminal abandonment lever — the supported replacement for the
hand-written ``INSERT INTO chunk_stopped`` the issue's motivating incident required.
Unlike ``pause`` (keeps the claim) and unlike ``detach`` (releases the route but writes
no terminal fact), stop does both in one operation: it writes the ``chunk_stopped``
fact *and* releases any live route, so the holding runner's own detach-discovery
(``test_runner_detach.py``'s route-only predicate) abandons the lease and frees the
environments on its next tick — no separate ``detach`` call needed. These tests prove
the controller wires that correctly end to end: 202/404/409, the fact written, the
route released, a held fleet-wide hub-exec slot released, the events published, and
that a stopped chunk never re-enters the ready queue. ``StopService``'s own refusal
matrix is unit-tested in ``test_stop_service.py``; the end-to-end environment release
a holding runner performs on its next tick is proven in
``test_hub_runner_seam.py::test_stop_at_the_real_hub_is_learned_by_a_real_pull_tick``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.work import IWriteChunkRepository
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


def _ask(hub, chunk_id: str, *, question_id: str = "qn_1") -> None:  # type: ignore[no-untyped-def]
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
    """Unlike detach's ``NotRouted`` 409, stop never requires a live route to release."""
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


def _peek_ids(hub) -> list[str]:  # type: ignore[no-untyped-def]
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


def test_stop_releases_a_held_fleet_wide_hub_exec_slot(tmp_path: Path) -> None:
    """Consider-4 from the #118 pre-push review: a ``delivering`` chunk holding the
    fleet-wide hub-exec slot is stoppable (stop only refuses {done, stopped}), and
    the slot release rides the same atomic write as the terminal fact — no waiting
    out ``stale_after`` before every other chunk's hub node can run again."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    chunks = _writable(hub)
    slot_id = chunks.acquire_hub_exec_slot(
        chunk_id, node_id="nd_deliver", at=hub.clock.now(), stale_after=timedelta(minutes=5)
    )
    assert slot_id is not None
    assert chunks.count_live_hub_exec_slots() == 1

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert chunks.count_live_hub_exec_slots() == 0
