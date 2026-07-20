"""The ``/chunks/{id}/pause`` and ``/resume`` routes over the HTTP surface (issue #46).

A pause keeps the claim — unlike detach, no route is released and no epoch bumped;
these routes only test the wire the operator lever rides: 200/404/409, the fact
written, the ``pause`` view on the detail, and the two events published (a pause moves
the chunk out of the ready queue, so ``queue-changed`` fires alongside ``chunk-changed``).
The refusal itself (``PauseService``) is unit-tested in ``test_pause_service.py``; this
file proves the controller wires it correctly end to end.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import insert as sa_insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.store import schema
from tests.support import (
    assert_all_timestamps_utc,
    build_hub,
    emitted_events,
    ingest,
    report_lease,
)

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

# The merge graph reaches `done`; the pending graph parks at `delivering` — its hub
# node's script prints the reserved `pending` outcome (#66), so no transition ever
# routes it onward and the chunk stays parked at the hub node. Neither status is
# otherwise reachable through a shorter path.
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

_PENDING_YAML = _MERGE_YAML.replace('command: "true"', 'command: "echo pending"')


def _claim(hub, chunk_id: str) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return resp.json()["envelope"]["node"]["node_id"]


def _build_completion(build_node_id: str, epoch: int) -> dict:
    return {
        "choice": "pass",
        "epoch": epoch,
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
    }


def _ingest_and_deliver(hub, *, yaml: str) -> str:  # type: ignore[no-untyped-def]
    """Register ``yaml`` as the (pre-minted) default graph, then ingest and drive one
    chunk through it to its deliver hub node — reusing that graph by name (D-081), so
    the ingest below picks it up instead of the packaged prose graph."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": yaml}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    build_node_id = _claim(hub, chunk_id)
    apply = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=_build_completion(build_node_id, 1))
    assert apply.status_code == 200, apply.text
    assert apply.json()["outcome"] == "hub_node_taken"
    return chunk_id


def _stop(tmp_path: Path, chunk_id: str, *, at: datetime) -> None:
    """Write a ``chunk_stopped`` row directly rather than through ``POST
    /chunks/{id}/stop`` (issue #118) — a lighter-weight precondition for a test that
    only needs a stopped chunk to exist, not to exercise the stop route itself (that
    route's own behavior is proven in ``test_chunk_stop.py``)."""
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        conn.execute(sa_insert(schema.chunk_stopped).values(chunk_id=chunk_id, stopped_at=at))


def test_pause_returns_200_writes_a_fact_and_the_detail_carries_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "alice"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"chunk_id": chunk_id}
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "paused"
    # Pinned field by field against the clock the hub was built with — not compared to
    # itself. `set_at` is a wire timestamp, so it carries an explicit UTC offset
    # (`bzh:utc-instants`); `assert_all_timestamps_utc` sweeps the whole payload the way
    # the other route tests (test_gates, test_runner_leases_api, …) do.
    assert detail["pause"] == {"by": "alice", "set_at": iso_utc(hub.clock.now())}
    assert_all_timestamps_utc(detail)


def test_pause_defaults_by_to_operator(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["pause"]["by"] == "operator"


def test_pause_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_nope/pause", json={"by": "operator"})
    assert resp.status_code == 404


def test_pause_refuses_a_done_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_and_deliver(hub, yaml=_MERGE_YAML)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"})

    assert resp.status_code == 409, resp.text
    assert "done" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["pause"] is None


def test_pause_refuses_a_delivering_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest_and_deliver(hub, yaml=_PENDING_YAML)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "delivering"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"})

    assert resp.status_code == 409, resp.text
    assert "delivering" in resp.json()["detail"]


def test_pause_refuses_a_stopped_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _stop(tmp_path, chunk_id, at=hub.clock.now())
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"})

    assert resp.status_code == 409, resp.text
    assert "stopped" in resp.json()["detail"]


def test_pause_allows_a_running_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert (
        hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
        ).status_code
        == 201
    )

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "paused"
    assert detail["route"] is not None  # unlike detach, the claim is kept


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


def test_pause_view_is_carried_even_when_the_status_hides_the_pause(tmp_path: Path) -> None:
    """THE keystone for P4: paused **and** parked on a question — status hides it, `pause` does not.

    `waiting_on_human` outranks `paused` in the derivation (§0.3 keeps the lever broad, so
    this overlap is reachable by design), which makes `status` a **lossy** read of "is this
    paused". `ChunkDetail.pause` is the runner's only non-lossy source, and P4 keys its
    kill-and-park on it. Proven here off a real HTTP response, over the real store: if this
    fails, P4 resumes paused workers on the answer (§3.3).
    """
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert (
        hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
        ).status_code
        == 201
    )
    _ask(hub, chunk_id)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "alice"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human", "the human gate still outranks the pause"
    assert detail["pause"] is not None, "the pause fact must stay legible behind the status — P4 reads this"
    assert detail["pause"]["by"] == "alice"


def test_resume_clears_the_pause_view_behind_a_hiding_status(tmp_path: Path) -> None:
    """The inverse of the keystone: the still-parked chunk's `pause` clears on resume.

    Guards the other half — an `open_pause` that ignored the newest fact's `paused` flag
    would keep reporting a pause here forever, and P4 would never restart the worker.
    """
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    _ask(hub, chunk_id)
    assert hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "alice"}).status_code == 202

    assert hub.client.post(f"/api/chunks/{chunk_id}/resume", json={"by": "bob"}).status_code == 202

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human", "the question is still open — only the pause cleared"
    assert detail["pause"] is None


def test_resume_returns_200_and_the_chunk_derives_ready_again(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"}).status_code == 202

    resp = hub.client.post(f"/api/chunks/{chunk_id}/resume", json={"by": "bob"})

    assert resp.status_code == 202, resp.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "ready"
    assert detail["pause"] is None


def test_resume_is_idempotent_on_an_unpaused_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])

    resp = hub.client.post(f"/api/chunks/{chunk_id}/resume", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"


def test_resume_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_nope/resume", json={"by": "operator"})
    assert resp.status_code == 404


def test_pause_publishes_both_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
    assert "queue-changed" in types
    assert any(chunk_id in e["data"] and '"status": "paused"' in e["data"] for e in events)


def test_resume_publishes_both_chunk_changed_and_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    assert hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"}).status_code == 202
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/resume", json={"by": "operator"})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
    assert "queue-changed" in types
