"""The in-transaction write fence over every runner-submitted write (component tier).

``bzh:epoch-fencing``: a stale or post-terminal write is refused and never recorded, the
verdict derived inside the recording transaction — so a stop or restart holding the chunk
row lock mid-write cannot be overtaken. Each path is pinned for a stale epoch, a stop, and
that race."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Table

from blizzard.foundation.migration_source import MigrationSource
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission, FenceRefusal
from blizzard.hub.store import schema as s
from tests.support import (
    HubHarness,
    build_hub,
    chunk_rows,
    chunk_stores,
    ingest,
    report_escalation,
    report_lease,
    report_question,
)

pytestmark = pytest.mark.component

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

_STALE = "stale epoch 1; chunk is at 2"
_TERMINAL = "chunk is terminal"


class _Chunk:
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

    def complete(self, node: str, *, epoch: int, choice: str, decision_id: str | None = None) -> dict:
        body: dict[str, object] = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": self.nodes[node],
            "artifacts": [],
        }
        if decision_id is not None:
            body["decision_id"] = decision_id
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/completions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def decide(self, node: str, *, epoch: int) -> dict:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/decisions",
            json={"from_node_id": self.nodes[node], "epoch": epoch, "runner_id": "r1", "artifacts": []},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def stop(self) -> None:
        assert self.hub.client.post(f"/api/chunks/{self.chunk_id}/stop", json={"by": "operator"}).status_code == 202

    def restart(self) -> None:
        assert self.hub.client.post(f"/api/chunks/{self.chunk_id}/restart", json={"by": "operator"}).status_code == 202

    def detail(self) -> dict:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def rows(self, table: Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)

    def gate_decision(self) -> str:
        assert self.complete("build", epoch=1, choice="pass")["outcome"] == "parked_at_gate"
        decision_id = str(self.detail()["decision"]["decision_id"])
        resolved = self.hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"})
        assert resolved.status_code == 200, resolved.text
        return decision_id


def _held_by_a_writer(chunk: _Chunk, submit: Callable[[], object], write: Callable[[object], None]) -> object:
    answered: dict[str, object] = {}

    def _submit() -> None:
        answered["result"] = submit()

    thread = threading.Thread(target=_submit)
    with chunk.stores.exclusive.locked([chunk.chunk_id]) as handle:
        thread.start()
        thread.join(timeout=0.3)
        assert thread.is_alive(), "the submission completed while a writer held the chunk row lock — not fenced"
        write(handle)
    thread.join(timeout=5)
    assert not thread.is_alive()
    return answered["result"]


def _stop_under_lock(chunk: _Chunk) -> Callable[[object], None]:
    def write(handle) -> None:  # type: ignore[no-untyped-def]
        chunk.stores.lifecycle.record_stop_locked(handle, chunk.chunk_id, by="operator", at=chunk.hub.clock.now())

    return write


def _restart_under_lock(chunk: _Chunk) -> Callable[[object], None]:
    def write(handle) -> None:  # type: ignore[no-untyped-def]
        chunk.stores.movement.record_restart_locked(
            handle,
            chunk.chunk_id,
            from_node_id=chunk.nodes["build"],
            to_node_id=chunk.nodes["build"],
            by="operator",
            at=chunk.hub.clock.now(),
        )

    return write


def test_a_stale_completion_is_refused_and_records_no_transition(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.lease(2)

    result = chunk.complete("build", epoch=1, choice="finish")

    assert result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.transitions) == 0


def test_a_completion_on_a_stopped_chunk_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.stop()

    result = chunk.complete("build", epoch=1, choice="finish")

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 0


def test_a_completion_at_the_current_epoch_after_done_is_refused_as_terminal(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"
    assert chunk.detail()["status"] == "done"

    result = chunk.complete("review", epoch=1, choice="pass")

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 1


def test_a_replayed_completion_still_absorbs_once_the_chunk_is_done(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"

    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"
    assert chunk.rows(s.transitions) == 1


def test_a_completion_blocks_behind_a_stop_and_then_refuses(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    result = _held_by_a_writer(
        chunk, lambda: chunk.complete("build", epoch=1, choice="finish"), _stop_under_lock(chunk)
    )

    assert isinstance(result, dict) and result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 0


def test_a_completion_blocks_behind_a_restart_and_then_refuses(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    result = _held_by_a_writer(
        chunk, lambda: chunk.complete("build", epoch=1, choice="finish"), _restart_under_lock(chunk)
    )

    assert isinstance(result, dict) and result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.transitions) == 0


def test_a_stale_gate_resolution_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    decision_id = chunk.gate_decision()
    chunk.lease(2)

    result = chunk.complete("approve-gate", epoch=1, choice="approve", decision_id=decision_id)

    assert result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.transitions) == 1  # only the arrival at the gate


def test_a_gate_resolution_on_a_stopped_chunk_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    decision_id = chunk.gate_decision()
    chunk.stop()

    result = chunk.complete("approve-gate", epoch=1, choice="approve", decision_id=decision_id)

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 1


def test_a_gate_resolution_blocks_behind_a_stop_and_then_refuses(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    decision_id = chunk.gate_decision()

    result = _held_by_a_writer(
        chunk,
        lambda: chunk.complete("approve-gate", epoch=1, choice="approve", decision_id=decision_id),
        _stop_under_lock(chunk),
    )

    assert isinstance(result, dict) and result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.transitions) == 1


def test_a_stale_runner_config_decision_is_refused_and_opens_nothing(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.lease(2)

    result = chunk.decide("build", epoch=1)

    assert result["outcome"] == "failure" and _STALE in result["detail"]
    assert chunk.rows(s.decisions) == 0


def test_a_runner_config_decision_on_a_stopped_chunk_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.stop()

    result = chunk.decide("build", epoch=1)

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.decisions) == 0


def test_a_runner_config_decision_after_done_is_refused_as_terminal(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"

    result = chunk.decide("review", epoch=1)

    assert result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.decisions) == 0


def test_a_runner_config_decision_blocks_behind_a_stop_and_then_refuses(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    result = _held_by_a_writer(chunk, lambda: chunk.decide("build", epoch=1), _stop_under_lock(chunk))

    assert isinstance(result, dict) and result["outcome"] == "failure" and result["detail"] == _TERMINAL
    assert chunk.rows(s.decisions) == 0


def _ask(chunk: _Chunk, *, epoch: int) -> dict:
    chunk.seq += 1
    return report_question(chunk.hub, chunk.chunk_id, epoch=epoch, seq=chunk.seq)


def _escalate(chunk: _Chunk, *, epoch: int) -> dict:
    chunk.seq += 1
    return report_escalation(chunk.hub, chunk.chunk_id, epoch=epoch, seq=chunk.seq)


def test_a_below_floor_question_after_a_restart_is_rejected_and_parks_nothing(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.restart()

    ack = _ask(chunk, epoch=1)

    assert ack["rejected"] == [chunk.seq] and ack["applied"] == []
    assert chunk.rows(s.questions) == 0
    assert chunk.detail()["status"] != "waiting_on_human"


def test_a_below_floor_escalation_after_a_restart_is_rejected_and_parks_nothing(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.restart()

    ack = _escalate(chunk, epoch=1)

    assert ack["rejected"] == [chunk.seq] and ack["applied"] == []
    assert chunk.rows(s.escalations) == 0
    assert chunk.detail()["status"] != "needs_human"


def test_a_question_and_an_escalation_on_a_stopped_chunk_are_rejected(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.stop()

    assert _ask(chunk, epoch=1)["rejected"] == [chunk.seq]
    assert _escalate(chunk, epoch=1)["rejected"] == [chunk.seq]
    assert chunk.rows(s.questions) == 0 and chunk.rows(s.escalations) == 0


def test_a_same_epoch_question_and_escalation_still_land(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    assert _ask(chunk, epoch=1)["applied"] == [chunk.seq]
    assert _escalate(chunk, epoch=1)["applied"] == [chunk.seq]


def test_a_replayed_question_absorbs_even_once_the_chunk_is_stopped(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert _ask(chunk, epoch=1)["applied"] == [chunk.seq]
    chunk.stop()

    ack = _ask(chunk, epoch=1)  # a fresh seq re-sending the same question id

    assert ack["applied"] == [chunk.seq] and ack["rejected"] == []
    assert chunk.rows(s.questions) == 1


def test_an_intake_blocks_behind_a_restart_and_then_rejects(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.seq += 1
    seq = chunk.seq

    def submit() -> dict:
        resp = chunk.hub.client.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [
                    {
                        "seq": seq,
                        "kind": "escalation.recorded",
                        "payload": {"chunk_id": chunk.chunk_id, "epoch": 1, "takeover_command": "x"},
                    }
                ],
            },
        )
        return resp.json()

    result = _held_by_a_writer(chunk, submit, _restart_under_lock(chunk))

    assert isinstance(result, dict) and result["rejected"] == [seq]
    assert chunk.rows(s.escalations) == 0


def test_the_store_returns_the_refusal_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _Chunk(hub)
    chunk.lease(2)

    refused = chunk.stores.escalations.record_escalation(
        chunk.chunk_id,
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        takeover_command="x",
        at=hub.clock.now(),
        cause=None,
        detail=None,
    )

    assert refused == FenceRefusal.stale(1, latest=2)
    assert chunk.rows(s.escalations) == 0


@pytest.mark.parametrize(
    ("admission", "epoch", "admitted"),
    [
        (EpochAdmission.CURRENT, 2, True),
        (EpochAdmission.CURRENT, 3, False),
        (EpochAdmission.AT_OR_ABOVE, 3, True),
        (EpochAdmission.AT_OR_ABOVE, 1, False),
        (EpochAdmission.ABOVE, 2, False),
        (EpochAdmission.ABOVE, 3, True),
    ],
)
def test_the_admission_table(admission: EpochAdmission, epoch: int, admitted: bool) -> None:
    assert admission.admits(epoch, newest=2) is admitted
    assert admission.admits(epoch, newest=0) is True  # no epoch minted yet — everything clears


def test_a_stale_or_stopped_migration_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _Chunk(hub)
    chunk.lease(2)

    def migrate(epoch: int) -> str | FenceRefusal | None:
        return chunk.stores.movement.record_migration(
            chunk.chunk_id,
            from_node_id=chunk.nodes["build"],
            from_graph_id="gr_from",
            to_graph_id="gr_to",
            landed_node_id=None,
            choice_name="pass",
            model=None,
            epoch=epoch,
            admission=EpochAdmission.CURRENT,
            at=hub.clock.now(),
            artifacts=[],
            proposals=[],
            source=MigrationSource.AUTHORED_EDGE,
        )

    assert migrate(1) == FenceRefusal.stale(1, latest=2)
    chunk.stop()
    assert migrate(2) == FenceRefusal.terminal(2)
    assert chunk.rows(s.chunk_migrations) == 0


def test_the_store_refuses_a_write_at_the_epoch_a_transition_reached_done_as_terminal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _Chunk(hub)
    assert chunk.complete("build", epoch=1, choice="finish")["outcome"] == "done"

    refused = chunk.stores.escalations.record_escalation(
        chunk.chunk_id,
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        takeover_command="x",
        at=hub.clock.now(),
        cause=None,
        detail=None,
    )

    assert refused == FenceRefusal.terminal(1)
    assert chunk.rows(s.escalations) == 0
