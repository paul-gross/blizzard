"""``GET /api/leases/{id}/notes`` and the person-made ``chunk history`` rows — component tier.

The hub side is real: a chunk is driven through a gate resolution, a hub requeue and a restart on
the hub's API, and the runner route is fed the ``ChunkDetail`` the hub's own projection then
serves on ``/api/fleet/chunks/{id}``. The runner reaches that payload through a stubbed
``httpx2.Client`` carrying it verbatim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.config import RunnerConfig
from blizzard.runner.leases.model import NewLease
from tests.config_inputs import create_runner_app
from tests.runner_client import runner_client
from tests.runner_fakes import make_host_stores, make_store, make_stores, no_retry_clock
from tests.support import build_hub, pointer_token, report_escalation, report_lease, report_question

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_HEADERS = {"X-Blizzard-Lease-Token": _TOKEN}

_GATE_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Built.
          to: signoff
  signoff:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Approved.
          to: done
        reject:
          description: Rejected.
          to: build
"""

_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}


def _hub_detail(tmp_path: Path, notes: tuple[str | None, str | None, str | None]) -> tuple[str, dict]:
    """Drive one chunk on a real hub through a resolution, a requeue and a restart, in that order,
    each carrying its note (``None`` sends none), and return the fleet ``ChunkDetail`` it serves."""
    resolve_note, requeue_note, restart_note = notes
    (tmp_path / "hub").mkdir()
    hub = build_hub(tmp_path / "hub")
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    assert graph.status_code == 201, graph.text
    build_id = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}["build"]
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "9"})]})
    chunk_id = chunk_id.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    parked = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_id, "artifacts": [_ARTIFACT]},
    )
    assert parked.json()["outcome"] == "parked_at_gate", parked.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]

    def with_note(body: dict, note: str | None) -> dict:
        return body if note is None else {**body, "note": note}

    hub.clock.advance(timedelta(seconds=1))
    resolved = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json=with_note({"choice": "reject"}, resolve_note)
    )
    assert resolved.status_code == 200, resolved.text
    report_lease(hub, chunk_id, epoch=2, seq=3)
    report_question(hub, chunk_id, epoch=2, seq=4)
    assert report_escalation(hub, chunk_id, epoch=2, seq=5)["applied"] == [5]
    hub.clock.advance(timedelta(seconds=1))
    requeued = hub.client.post(f"/api/chunks/{chunk_id}/requeues", json=with_note({}, requeue_note))
    assert requeued.status_code == 202, requeued.text
    hub.clock.advance(timedelta(seconds=1))
    restarted = hub.client.post(
        f"/api/chunks/{chunk_id}/restart", json=with_note({"by": "ada", "node": "build"}, restart_note)
    )
    assert restarted.status_code == 202, restarted.text

    detail = hub.client.get(f"/api/fleet/chunks/{chunk_id}")
    assert detail.status_code == 200, detail.text
    return chunk_id, detail.json()


def _runner_serving(tmp_path: Path, chunk_id: str, detail: dict):
    """A runner whose hub forward answers ``detail`` verbatim, with an open lease on ``chunk_id``."""
    tmp_path.mkdir(exist_ok=True)
    db_url = f"sqlite:///{tmp_path / 'runner.db'}"
    store = make_store(db_url)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == f"/api/fleet/chunks/{chunk_id}"
        return httpx2.Response(200, json=detail)

    app = create_runner_app(
        RunnerConfig(root=tmp_path, db_url=db_url, hub_url=_HUB_URL),
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store),
        hub_proxy_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        hub_retry_clock=no_retry_clock(),
    )
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=3,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_lease_token("lease_1", TokenHash(_TOKEN).hex, _NOW)
    return app


def test_noted_resolve_requeue_and_restart_reach_chunk_notes_oldest_first(tmp_path: Path) -> None:
    chunk_id, detail = _hub_detail(tmp_path, ("first", "second", "third"))

    with runner_client(_runner_serving(tmp_path, chunk_id, detail)) as client:
        resp = client.get("/api/leases/lease_1/notes", headers=_HEADERS)

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [(r["kind"], r["note"]) for r in rows] == [
        ("resolution", "first"),
        ("requeue", "second"),
        ("restart", "third"),
    ]
    assert (rows[0]["node"], rows[0]["choice"], rows[0]["by"]) == ("signoff", "reject", "operator")
    assert (rows[1]["node"], rows[1]["choice"], rows[1]["by"]) == (None, None, "operator")
    assert (rows[2]["node"], rows[2]["by"]) == ("build", "ada")
    assert [r["recorded_at"] for r in rows] == sorted(r["recorded_at"] for r in rows)


def test_history_shows_a_row_per_resolution_requeue_and_restart(tmp_path: Path) -> None:
    chunk_id, detail = _hub_detail(tmp_path, ("first", None, "third"))

    with runner_client(_runner_serving(tmp_path, chunk_id, detail)) as client:
        resp = client.get("/api/leases/lease_1/history", headers=_HEADERS)

    assert resp.status_code == 200, resp.text
    person_made = [r for r in resp.json() if r["kind"] in {"resolution", "requeue", "restart"}]
    assert [(r["kind"], r["by"], r["note"]) for r in person_made] == [
        ("resolution", "operator", "first"),
        ("requeue", "operator", None),
        ("restart", "ada", "third"),
    ]
    assert (person_made[0]["from_node"], person_made[0]["choice"]) == ("signoff", "reject")


def test_without_notes_chunk_notes_is_empty_and_chunk_asks_is_unchanged(tmp_path: Path) -> None:
    chunk_id, detail = _hub_detail(tmp_path, (None, None, None))
    stripped = {k: v for k, v in detail.items() if k not in {"resolutions", "requeues", "restarts"}}

    with runner_client(_runner_serving(tmp_path / "noted", chunk_id, detail)) as client:
        notes = client.get("/api/leases/lease_1/notes", headers=_HEADERS)
        asks = client.get("/api/leases/lease_1/asks", headers=_HEADERS)
    with runner_client(_runner_serving(tmp_path / "bare", chunk_id, stripped)) as client:
        bare_asks = client.get("/api/leases/lease_1/asks", headers=_HEADERS)

    assert notes.status_code == 200, notes.text
    assert notes.json() == []
    assert asks.status_code == 200, asks.text
    assert [r["question_id"] for r in asks.json()] == ["q_1"]
    assert asks.content == bare_asks.content
