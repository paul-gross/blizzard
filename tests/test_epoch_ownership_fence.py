"""Component checks for epoch ownership after restart or reclaim.

Displaced mints and writes at the successor's epoch are refused regardless of
drain order (``bzh:epoch-fencing``). Warn-mode route tokens isolate this fence."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, ingest

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
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: |
      Review the build.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Reviewed.
          to: done
"""


class _Runner:
    """One runner's view of the chunk: its own fact seq and, while it holds one, its route token."""

    def __init__(self, hub: HubHarness, chunk_id: str, runner_id: str) -> None:
        self.hub = hub
        self.chunk_id = chunk_id
        self.runner_id = runner_id
        self.route_token: str | None = None
        self.seq = 0

    def claim(self) -> int:
        resp = self.hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": self.runner_id, "workspace_id": "w", "environment_ids": []},
        )
        assert resp.status_code == 201, resp.text
        self.route_token = resp.json()["route_token"]
        return int(resp.json()["envelope"]["epoch"])

    def detached(self) -> None:
        """The route went away under this runner: it has no valid token to present any more."""
        self.route_token = None

    def _push(self, kind: str, payload: dict[str, object]) -> dict:
        self.seq += 1
        if self.route_token is not None:
            payload = {**payload, "route_token": self.route_token}
        resp = self.hub.client.post(
            "/api/fleet/events",
            json={"runner_id": self.runner_id, "facts": [{"seq": self.seq, "kind": kind, "payload": payload}]},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def mint(self, epoch: int) -> bool:
        """Report a ``lease.minted`` at ``epoch``; whether the hub admitted it."""
        ack = self._push("lease.minted", {"chunk_id": self.chunk_id, "epoch": epoch})
        return self.seq in ack["applied"]

    def ask(self, epoch: int) -> bool:
        ack = self._push(
            "question.asked",
            {
                "question_id": f"qn_{self.runner_id}_{self.seq + 1}",
                "chunk_id": self.chunk_id,
                "runner_id": self.runner_id,
                "epoch": epoch,
                "question": "Which API?",
                "options": ["rest", "graphql"],
                "asked_at": "2026-07-13T00:00:00+00:00",
            },
        )
        return self.seq in ack["applied"]

    def escalate(self, epoch: int) -> bool:
        ack = self._push("escalation.recorded", {"chunk_id": self.chunk_id, "epoch": epoch, "takeover_command": "x"})
        return self.seq in ack["applied"]

    def ask_directly(self, epoch: int) -> int:
        resp = self.hub.client.post(
            "/api/questions",
            json={
                "question_id": f"qn_{self.runner_id}_direct_{epoch}",
                "chunk_id": self.chunk_id,
                "runner_id": self.runner_id,
                "epoch": epoch,
                "question": "Which API?",
                "options": ["rest", "graphql"],
                "asked_at": "2026-07-13T00:00:00+00:00",
            },
        )
        return resp.status_code

    def complete(self, node_id: str, *, epoch: int) -> dict:
        body: dict[str, object] = {
            "choice": "pass",
            "epoch": epoch,
            "runner_id": self.runner_id,
            "from_node_id": node_id,
        }
        if self.route_token is not None:
            body["route_token"] = self.route_token
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/completions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def decide(self, node_id: str, *, epoch: int) -> dict:
        body: dict[str, object] = {"from_node_id": node_id, "epoch": epoch, "runner_id": self.runner_id}
        if self.route_token is not None:
            body["route_token"] = self.route_token
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/decisions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()


class _Chunk:
    def __init__(self, tmp_path: Path) -> None:
        self.hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_WARN)
        graph = self.hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
        self.chunk_id = ingest(self.hub, [{"source": "default", "ref": "1"}])

    def runner(self, runner_id: str) -> _Runner:
        return _Runner(self.hub, self.chunk_id, runner_id)

    def detail(self) -> dict:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def restart(self) -> None:
        resp = self.hub.client.post(f"/api/chunks/{self.chunk_id}/restart", json={"by": "operator"})
        assert resp.status_code == 202, resp.text

    def detach(self) -> None:
        assert self.hub.client.post(f"/api/chunks/{self.chunk_id}/detach").status_code == 202

    def rows(self, table) -> int:  # type: ignore[no-untyped-def]
        with self.hub.engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(table).where(table.c.chunk_id == self.chunk_id)
            ).scalar_one()


def _refused(result: dict, *, epoch: int) -> None:
    assert result["outcome"] == "failure", result
    assert f"epoch {epoch}" in result["detail"]


# --- a claim reserves the next epoch, and the claimant's own mint lands on it ----------


def test_a_claim_reserves_the_next_epoch_for_its_claimant(tmp_path: Path) -> None:
    chunk = _Chunk(tmp_path)
    a = chunk.runner("rA")

    assert a.claim() == 0  # the envelope still carries the pre-reservation floor
    assert chunk.detail()["latest_epoch"] == 1  # ...and the reservation already raised the fence

    assert a.mint(1)
    assert chunk.detail()["latest_epoch"] == 1


# --- a restart lands level with runner A's unreported mint -----------------------------


def _restarted_level_with_a(tmp_path: Path) -> tuple[_Chunk, _Runner]:
    """A holds the chunk at epoch 1, mints 2 without reporting it, and a restart takes 2."""
    chunk = _Chunk(tmp_path)
    a = chunk.runner("rA")
    a.claim()
    assert a.mint(1)
    chunk.restart()
    assert chunk.detail()["latest_epoch"] == 2
    return chunk, a


def test_a_mint_level_with_a_restart_is_refused(tmp_path: Path) -> None:
    chunk, a = _restarted_level_with_a(tmp_path)

    assert not a.mint(2)
    assert chunk.rows(s.lease_facts) == 1


def test_a_completion_level_with_a_restart_is_refused_and_the_chunk_does_not_advance(tmp_path: Path) -> None:
    chunk, a = _restarted_level_with_a(tmp_path)
    a.mint(2)

    _refused(a.complete(chunk.nodes["build"], epoch=2), epoch=2)

    assert chunk.detail()["current_node_id"] == chunk.nodes["build"]
    assert chunk.rows(s.transitions) == 0


def test_a_decision_level_with_a_restart_is_refused_and_opens_nothing(tmp_path: Path) -> None:
    chunk, a = _restarted_level_with_a(tmp_path)
    a.mint(2)

    _refused(a.decide(chunk.nodes["build"], epoch=2), epoch=2)

    assert chunk.rows(s.decisions) == 0
    assert chunk.detail()["current_node_id"] == chunk.nodes["build"]


def test_a_question_and_an_escalation_level_with_a_restart_are_refused(tmp_path: Path) -> None:
    chunk, a = _restarted_level_with_a(tmp_path)

    assert not a.ask(2)
    assert a.ask_directly(2) == 409
    assert not a.escalate(2)

    assert chunk.rows(s.questions) == 0
    assert chunk.rows(s.escalations) == 0
    assert chunk.detail()["status"] == "running"


def test_after_a_restart_the_holder_mints_above_it_and_advances(tmp_path: Path) -> None:
    chunk, a = _restarted_level_with_a(tmp_path)
    assert not a.mint(2)

    assert a.mint(3)
    result = a.complete(chunk.nodes["build"], epoch=3)

    assert result["outcome"] == "next", result
    assert chunk.detail()["current_node_id"] == chunk.nodes["review"]


# --- a reclaim: A detached with a buffered mint, B claims --------------------------------


def _reclaimed_by_b(tmp_path: Path) -> tuple[_Chunk, _Runner, _Runner]:
    """A holds epoch 1 and is detached; B claims, reserving epoch 2 — A's next local mint."""
    chunk = _Chunk(tmp_path)
    a = chunk.runner("rA")
    a.claim()
    assert a.mint(1)
    chunk.detach()
    a.detached()
    b = chunk.runner("rB")
    assert b.claim() == 1
    assert chunk.detail()["latest_epoch"] == 2
    return chunk, a, b


def _b_completes(chunk: _Chunk, b: _Runner) -> None:
    result = b.complete(chunk.nodes["build"], epoch=2)
    assert result["outcome"] == "next", result
    assert chunk.detail()["current_node_id"] == chunk.nodes["review"]
    assert chunk.detail()["route"]["runner_id"] == "rB"


def test_a_displaced_mint_drained_before_the_successors_is_refused(tmp_path: Path) -> None:
    chunk, a, b = _reclaimed_by_b(tmp_path)

    assert not a.mint(2)
    _refused(a.complete(chunk.nodes["build"], epoch=2), epoch=2)
    assert b.mint(2)

    assert chunk.rows(s.transitions) == 0
    _b_completes(chunk, b)


def test_a_displaced_mint_drained_after_the_successors_is_refused(tmp_path: Path) -> None:
    chunk, a, b = _reclaimed_by_b(tmp_path)

    assert b.mint(2)
    assert not a.mint(2)
    _refused(a.complete(chunk.nodes["build"], epoch=2), epoch=2)

    assert chunk.rows(s.transitions) == 0
    _b_completes(chunk, b)


def test_several_displaced_mints_above_the_reservation_are_refused(tmp_path: Path) -> None:
    """A ran on past its detach — mints 2, 3, 4 buffered. Only the live route's holder takes
    a fresh epoch, so the ones above B's reservation bounce too."""
    chunk, a, b = _reclaimed_by_b(tmp_path)

    assert [a.mint(epoch) for epoch in (2, 3, 4)] == [False, False, False]
    _refused(a.complete(chunk.nodes["build"], epoch=4), epoch=4)
    assert chunk.detail()["latest_epoch"] == 2

    assert b.mint(2)
    _b_completes(chunk, b)
    assert b.mint(3)
    assert b.complete(chunk.nodes["review"], epoch=3)["outcome"] == "done"
