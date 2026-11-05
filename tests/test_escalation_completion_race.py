"""A completion that escalates decides inside its own write (component tier, SQLite).

Two completions from the lease holding one ``(node, epoch)`` race: the competing one commits
after the escalating one's apply has read the chunk's facts and before its escalation write
opens. The escalating completion must decide from facts re-read under the chunk's lock — a
movement that already landed answers as its replay, the same escalation already written answers
as itself and writes nothing, and a different one refuses — so ``(node, epoch)`` never holds a
second escalation, nor one on a chunk that moved off the node."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Table

from blizzard.hub.domain.execution.apply import ApplyService
from blizzard.hub.store import schema as s
from tests.support.scaffolding import HubHarness, build_hub, chunk_rows, ingest, report_lease

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
          to: done
        review:
          description: Ready for review.
          to: review
        hand-off:
          description: Hand off to another graph.
          to: graph:ghost
        unfulfillable:
          description: A step no worker can complete.
          to: escalate
        blocked:
          description: Blocked on something outside the repository.
          to: escalate
  review:
    executor: runner
    prompt: |
      Review the change.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Approved.
          to: done
"""

_PARKED = "parked_at_gate"
_ESCALATED = "escalated — requeue the chunk"


class _Chunk:
    """One claimed chunk at ``build``, epoch 1, whose completions post through the fleet route."""

    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(self.hub, self.chunk_id, epoch=1, seq=1)

    def complete(self, choice: str) -> dict[str, Any]:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/completions",
            json={"choice": choice, "epoch": 1, "runner_id": "r1", "from_node_id": self.node_id, "artifacts": []},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def detail(self) -> dict[str, Any]:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def rows(self, table: Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)


def _race(
    monkeypatch: pytest.MonkeyPatch, chunk: _Chunk, hook: str, *, winner: str, loser: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Submit ``loser``, committing ``winner`` once ``loser``'s apply has read its facts and
    reached the escalation path ``hook``, before that path writes. Returns both answers."""
    original: Callable[..., Any] = getattr(ApplyService, hook)
    won: list[dict[str, Any]] = []

    def interleave(self: ApplyService, *args: Any, **kwargs: Any) -> Any:
        if not won:
            won.append({})
            won[0] = chunk.complete(winner)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ApplyService, hook, interleave)
    lost = chunk.complete(loser)
    assert won, f"the race never reached `{hook}`"
    return won[0], lost


def test_a_duplicate_escalate_choice_writes_one_escalation_and_both_answer_parked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    won, lost = _race(monkeypatch, chunk, "_escalate_choice", winner="unfulfillable", loser="unfulfillable")

    assert won["outcome"] == _PARKED and lost["outcome"] == _PARKED
    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.transitions) == 0


def test_a_different_escalate_choice_after_one_landed_is_refused_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    won, lost = _race(monkeypatch, chunk, "_escalate_choice", winner="blocked", loser="unfulfillable")

    assert won["outcome"] == _PARKED
    assert lost["outcome"] == "failure" and _ESCALATED in lost["detail"]
    assert chunk.rows(s.escalations) == 1
    assert "`blocked`" in chunk.detail()["escalation"]["detail"]


def test_an_escalate_choice_after_a_transition_onward_answers_as_that_transitions_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    won, lost = _race(monkeypatch, chunk, "_escalate_choice", winner="review", loser="unfulfillable")

    assert won["outcome"] == "next"
    assert lost["outcome"] == "next"
    assert lost["next_envelope"]["node"]["node_name"] == won["next_envelope"]["node"]["node_name"] == "review"
    assert chunk.rows(s.escalations) == 0 and chunk.rows(s.transitions) == 1
    assert chunk.detail()["status"] != "needs_human"


def test_an_escalate_choice_after_a_transition_to_the_terminal_answers_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    won, lost = _race(monkeypatch, chunk, "_escalate_choice", winner="pass", loser="unfulfillable")

    assert won["outcome"] == "done" and lost["outcome"] == "done"
    assert chunk.rows(s.escalations) == 0 and chunk.rows(s.transitions) == 1


def test_a_duplicate_unresolved_cross_graph_completion_writes_one_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    won, lost = _race(monkeypatch, chunk, "_migrate_across", winner="hand-off", loser="hand-off")

    assert won["outcome"] == _PARKED and lost["outcome"] == _PARKED
    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.chunk_migrations) == 0
