"""The hub denies a claim on an unmet dependency, under the claim lock (blizzard#458,
component tier).

Mirrors the terminal denial's shape (``tests/test_route_claim.py``): a distinct 409 body,
refused outright rather than lost to a race, re-derived fresh under the shared claim lock
so a peek-then-claim window can never slip a blocked chunk through."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _resolve(hub: HubHarness, chunk_id: str):  # type: ignore[no-untyped-def]
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    return chunk


def _claim_body(chunk_id: str, runner: str = "r1") -> dict:
    return {"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": ["env-a"]}


def test_claim_denied_when_the_dependency_is_unmet(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    hub.services.dependencies.declare(_resolve(hub, dependent_id), _resolve(hub, prerequisite_id), by="user:alice")

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id))

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["chunk_id"] == dependent_id
    assert body["prerequisite_chunk_id"] == prerequisite_id
    # Distinguishable from the other two 409 shapes.
    assert "held_by_runner_id" not in body
    assert "status" not in body
    # The claim did not sneak a route onto the blocked chunk before refusing.
    assert hub.client.get(f"/api/chunks/{dependent_id}").json()["route"] is None


def test_claim_allowed_once_the_prerequisite_reaches_done(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    hub.services.dependencies.declare(_resolve(hub, dependent_id), _resolve(hub, prerequisite_id), by="user:alice")
    assert hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id)).status_code == 409

    hub.services.complete.complete(_resolve(hub, prerequisite_id), by="user:alice")
    resp = hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id))

    assert resp.status_code == 201, resp.text
    assert hub.client.get(f"/api/chunks/{dependent_id}").json()["status"] == "running"


def test_claim_allowed_against_a_prerequisite_already_done_before_the_edge_declared(tmp_path: Path) -> None:
    """An edge named against a chunk that is already done is accepted and is simply
    already satisfied — no special case at claim time either."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    hub.services.complete.complete(_resolve(hub, prerequisite_id), by="user:alice")
    hub.services.dependencies.declare(_resolve(hub, dependent_id), _resolve(hub, prerequisite_id), by="user:alice")

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id))

    assert resp.status_code == 201, resp.text


def test_claim_allowed_once_the_edge_is_released(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    edge = hub.services.dependencies.declare(
        _resolve(hub, dependent_id), _resolve(hub, prerequisite_id), by="user:alice"
    )
    assert hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id)).status_code == 409

    hub.services.dependencies.release(edge, by="user:bob")
    resp = hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id))

    assert resp.status_code == 201, resp.text


def test_claim_denied_the_instant_the_edge_is_declared_mid_tick(tmp_path: Path) -> None:
    """The peek-then-claim race the acceptance criteria names directly: a runner peeked
    before the edge existed, but the claim re-derives the standing set fresh under the
    lock rather than trusting anything read before it — proven here by patching the
    dependency store's write to pause, then racing the claim against it while it holds
    the shared lock (mirrors ``tests/test_dependency_race.py``'s pattern)."""
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prereq"}], promote=False)
    dependent = _resolve(hub, dependent_id)
    prerequisite = _resolve(hub, prerequisite_id)

    entered_write = threading.Event()
    release_write = threading.Event()
    dependencies_store = cast(IWriteChunkDependenciesRepository, hub.services.chunks.dependencies)
    real_declare = dependencies_store.declare

    def _blocking_declare(dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at):  # type: ignore[no-untyped-def]
        entered_write.set()
        assert release_write.wait(timeout=5), "test never released the declaration's write"
        return real_declare(dependent_chunk_id, prerequisite_chunk_id, by=by, at=at)

    dependencies_store.declare = _blocking_declare  # type: ignore[method-assign]

    declare_thread = threading.Thread(
        target=lambda: hub.services.dependencies.declare(dependent, prerequisite, by="user:alice")
    )
    declare_thread.start()
    assert entered_write.wait(timeout=5), "the declaration never reached its (patched) write"

    claim_result: dict[str, int] = {}

    def _claim() -> None:
        claim_result["status"] = hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id)).status_code

    claim_thread = threading.Thread(target=_claim)
    claim_thread.start()
    claim_thread.join(timeout=0.3)
    assert claim_thread.is_alive(), "the claim completed while the declaration still held the shared lock"

    release_write.set()
    declare_thread.join(timeout=5)
    claim_thread.join(timeout=5)

    assert claim_result["status"] == 409


def test_claim_denial_names_the_earliest_declared_unmet_prerequisite(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}], promote=False)
    first_prereq_id = ingest(hub, [{"source": "default", "ref": "prereq-1"}], promote=False)
    second_prereq_id = ingest(hub, [{"source": "default", "ref": "prereq-2"}], promote=False)
    dependent = _resolve(hub, dependent_id)
    hub.services.dependencies.declare(dependent, _resolve(hub, first_prereq_id), by="user:alice")
    hub.clock.advance(timedelta(seconds=1))  # a distinct `declared_at` — the store's own total order
    hub.services.dependencies.declare(dependent, _resolve(hub, second_prereq_id), by="user:alice")

    resp = hub.client.post("/api/fleet/routes", json=_claim_body(dependent_id))

    assert resp.status_code == 409, resp.text
    assert resp.json()["prerequisite_chunk_id"] == first_prereq_id
