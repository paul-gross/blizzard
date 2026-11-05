"""Helpers for the write fence tests, shared with other test modules."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import Table

from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from tests.support.scaffolding import (
    HubHarness,
    chunk_rows,
    chunk_stores,
    ingest,
    report_lease,
    with_test_lock_wait,
)

_YAML = """
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
          to: approve-gate
        finish:
          description: Nothing left to review.
          to: done
        fail:
          description: Incomplete.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it.
          to: done
        reject:
          description: Send it back.
          to: build
  review:
    executor: runner
    judgement:
      prompt: |
        Review the build.
      choices:
        pass:
          description: Reviewed.
          to: done
"""


# Carried by every fenced submission: a refusal must leave no artifact row behind either.
_ARTIFACT = {"name": "notes", "kind": "asset", "content": "n"}


class Chunk:
    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        self.stores = chunk_stores(hub.engine, hub.clock)
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        self.seq = 0
        self.lease(1)

    def lease(self, epoch: int) -> None:
        self.seq += 1
        report_lease(self.hub, self.chunk_id, epoch=epoch, seq=self.seq)

    def complete(self, node: str, *, epoch: int, choice: str, decision_id: str | None = None) -> dict[str, Any]:
        body: dict[str, object] = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": self.nodes[node],
            "artifacts": [_ARTIFACT],
        }
        if decision_id is not None:
            body["decision_id"] = decision_id
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/completions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def decide(self, node: str, *, epoch: int) -> dict[str, Any]:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/decisions",
            json={"from_node_id": self.nodes[node], "epoch": epoch, "runner_id": "r1", "artifacts": [_ARTIFACT]},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def stop(self) -> None:
        assert self.hub.client.post(f"/api/chunks/{self.chunk_id}/stop", json={"by": "operator"}).status_code == 202

    def restart(self) -> None:
        assert self.hub.client.post(f"/api/chunks/{self.chunk_id}/restart", json={"by": "operator"}).status_code == 202

    def detail(self) -> dict[str, Any]:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def rows(self, table: Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)

    def gate_decision(self) -> str:
        assert self.complete("build", epoch=1, choice="pass")["outcome"] == "parked_at_gate"
        decision_id = str(self.detail()["decision"]["decision_id"])
        resolved = self.hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"})
        assert resolved.status_code == 200, resolved.text
        return decision_id


def held_by_a_writer(chunk: Chunk, submit: Callable[[], object], write: Callable[[ILockedChunkRead], None]) -> object:
    with_test_lock_wait(chunk.hub)
    answered: dict[str, object] = {}
    started = threading.Event()

    def _submit() -> None:
        started.set()
        try:
            answered["result"] = submit()
        except BaseException as error:
            answered["error"] = error

    thread = threading.Thread(target=_submit)
    with chunk.stores.exclusive.locked([chunk.chunk_id]) as handle:
        thread.start()
        assert started.wait(timeout=60), "the submission thread never started"
        thread.join(timeout=0.3)
        assert thread.is_alive(), "the submission completed while a writer held the chunk row lock — not fenced"
        assert not answered, "the submission produced an outcome while a writer held the chunk row lock — not fenced"
        write(handle)
    # A generous hang guard: the blocked submission polls for the lock, then runs the whole request.
    thread.join(timeout=60)
    assert not thread.is_alive(), "the submission never finished after the writer released the chunk row lock"
    if "error" in answered:
        error = answered["error"]
        assert isinstance(error, BaseException)
        raise AssertionError("the submission raised") from error
    return answered["result"]


def stop_under_lock(chunk: Chunk) -> Callable[[ILockedChunkRead], None]:
    def write(handle: ILockedChunkRead) -> None:
        chunk.stores.lifecycle.record_stop_locked(handle, chunk.chunk_id, by="operator", at=chunk.hub.clock.now())

    return write


def restart_under_lock(chunk: Chunk) -> Callable[[ILockedChunkRead], None]:
    def write(handle: ILockedChunkRead) -> None:
        chunk.stores.movement.record_restart_locked(
            handle,
            chunk.chunk_id,
            from_node_id=chunk.nodes["build"],
            to_node_id=chunk.nodes["build"],
            by="operator",
            at=chunk.hub.clock.now(),
        )

    return write
