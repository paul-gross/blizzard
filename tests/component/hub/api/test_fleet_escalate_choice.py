"""The reserved ``escalate`` choice target, driven through the fleet routes (component tier).

A worker taking a choice whose ``to:`` is ``escalate`` parks the chunk ``needs_human`` with an
``escalate-choice`` escalation and records no transition. A repeat of the same choice is answered
as the escalation it is, any other verdict is refused, and a requeue re-runs the node."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Table, event, select

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_escalations_store
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    chunk_rows,
    emitted_events,
    hub_scoped,
    ingest,
    report_lease,
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
          to: done
        unfulfillable:
          description: A step no worker can complete.
          to: escalate
        blocked:
          description: Blocked on something outside the repository.
          to: escalate
        ghost:
          description: Hand off to a graph never minted.
          to: graph:ghost
        phantom:
          description: Hand off to another graph never minted.
          to: graph:phantom
"""

_ESCALATED = "escalated — requeue the chunk"
_PARKED = "parked_at_gate"


class _Chunk:
    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.node_id = graph.json()["nodes"][0]["node_id"]
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, self.chunk_id, epoch=1, seq=1)

    def complete(
        self,
        choice: str,
        *,
        epoch: int = 1,
        decision_id: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": "r1",
            "from_node_id": self.node_id,
            "artifacts": artifacts or [],
        }
        if decision_id is not None:
            body["decision_id"] = decision_id
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/completions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def detail(self) -> dict[str, Any]:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def rows(self, table: Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)

    def artifact_rows(self) -> list[tuple[str, str, int, str | None]]:
        with self.hub.engine.connect() as conn:
            found = conn.execute(
                hub_scoped(
                    select(s.artifacts.c.name, s.artifacts.c.node_name, s.artifacts.c.epoch, s.artifacts.c.data)
                    .where(s.artifacts.c.chunk_id == self.chunk_id)
                    .order_by(s.artifacts.c.seq)
                )
            ).all()
        return [(r.name, r.node_name, r.epoch, r.data) for r in found]


_ARTIFACTS: list[dict[str, Any]] = [
    {"name": "reasoning", "kind": "asset", "content": "cannot be built by a worker"},
    {"name": "notes", "kind": "asset", "content": "see the spike"},
    {
        "name": "work",
        "kind": "git_commit",
        "repo": "acme/widget",
        "branch_name": "blizzard/ch-1",
        "commit_hash": "a" * 40,
    },
]


def test_the_escalate_choice_parks_the_chunk_needs_human_and_records_no_transition(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    detail = chunk.detail()
    assert detail["status"] == "needs_human"
    assert detail["escalation"]["cause"] == "escalate-choice"
    assert "`build`" in detail["escalation"]["detail"] and "`unfulfillable`" in detail["escalation"]["detail"]
    assert detail["current_node_id"] == chunk.node_id
    assert chunk.rows(s.transitions) == 0 and chunk.rows(s.escalations) == 1


def test_the_escalate_choice_carries_operator_guidance_and_no_wrapped_takeover_verb(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    escalation = chunk.detail()["escalation"]

    assert escalation["wrapped_takeover_command"] == ""
    assert "requeue this chunk with a note" in escalation["takeover_command"]


def test_resubmitting_the_escalate_choice_answers_as_escalated_and_writes_nothing(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.transitions) == 0


def test_a_different_choice_after_the_escalate_choice_is_refused_and_leaves_it_open(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    refused = chunk.complete("pass")

    assert refused["outcome"] == "failure" and _ESCALATED in refused["detail"]
    assert chunk.rows(s.transitions) == 0
    assert chunk.detail()["status"] == "needs_human"


def test_a_gate_decision_resolved_to_the_escalate_choice_escalates_and_closes(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    decided = chunk.hub.client.post(
        f"/api/fleet/chunks/{chunk.chunk_id}/decisions",
        json={"from_node_id": chunk.node_id, "epoch": 1, "runner_id": "r1", "artifacts": []},
    )
    assert decided.status_code == 200, decided.text
    decision_id = chunk.detail()["decision"]["decision_id"]
    resolved = chunk.hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "unfulfillable"})
    assert resolved.status_code == 200, resolved.text

    assert chunk.complete("unfulfillable", decision_id=decision_id)["outcome"] == _PARKED

    detail = chunk.detail()
    assert detail["escalation"]["cause"] == "escalate-choice"
    assert detail.get("decision") is None
    assert chunk.rows(s.transitions) == 0


def test_a_requeue_supersedes_the_escalate_choice_and_re_runs_the_node(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    chunk.hub.clock.advance(timedelta(seconds=1))
    assert chunk.hub.client.post(f"/api/chunks/{chunk.chunk_id}/requeues").status_code == 202

    detail = chunk.detail()
    assert detail["status"] == "ready"
    assert detail["current_node_id"] == chunk.node_id
    assert detail.get("escalation") is None


def test_a_second_escalate_choice_after_the_first_is_refused_not_answered_as_its_replay(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    refused = chunk.complete("blocked")

    assert refused["outcome"] == "failure" and _ESCALATED in refused["detail"]
    assert chunk.rows(s.escalations) == 1


def test_an_unresolvable_cross_graph_choice_after_the_escalate_choice_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable")["outcome"] == _PARKED

    refused = chunk.complete("ghost")

    assert refused["outcome"] == "failure" and _ESCALATED in refused["detail"]
    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.transitions) == 0


def test_a_different_unresolvable_cross_graph_choice_after_one_that_escalated_is_refused(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("ghost")["outcome"] == _PARKED

    refused = chunk.complete("phantom")

    assert refused["outcome"] == "failure" and _ESCALATED in refused["detail"]
    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.transitions) == 0


def test_resubmitting_the_unresolvable_cross_graph_choice_is_still_answered_quietly(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("ghost")["outcome"] == _PARKED

    assert chunk.complete("ghost")["outcome"] == _PARKED

    assert chunk.rows(s.escalations) == 1


def test_the_escalate_choice_persists_its_artifacts_with_the_escalation(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    assert chunk.complete("unfulfillable", artifacts=_ARTIFACTS)["outcome"] == _PARKED

    rows = chunk.artifact_rows()
    assert [(n, node, epoch) for n, node, epoch, _ in rows] == [
        ("reasoning", "build", 1),
        ("notes", "build", 1),
        ("work", "build", 1),
    ]
    assert rows[0][3] == "cannot be built by a worker"
    assert chunk.rows(s.escalations) == 1 and chunk.rows(s.transitions) == 0
    assert {a["name"] for a in chunk.detail()["artifacts"]} >= {"reasoning", "notes", "work"}


def test_resubmitting_the_escalate_choice_with_other_artifacts_adds_none(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable", artifacts=_ARTIFACTS)["outcome"] == _PARKED
    before = chunk.artifact_rows()

    other = [{"name": "extra", "kind": "asset", "content": "later"}]
    assert chunk.complete("unfulfillable", artifacts=other)["outcome"] == _PARKED

    assert chunk.artifact_rows() == before


def test_a_losing_different_escalate_choice_adds_none_of_its_artifacts(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable", artifacts=_ARTIFACTS)["outcome"] == _PARKED
    before = chunk.artifact_rows()

    refused = chunk.complete("blocked", artifacts=[{"name": "extra", "kind": "asset", "content": "x"}])

    assert refused["outcome"] == "failure"
    assert chunk.artifact_rows() == before


def test_a_stale_epoch_escalate_choice_writes_no_escalation_and_no_artifacts(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    refused = chunk.complete("unfulfillable", epoch=2, artifacts=_ARTIFACTS)

    assert refused["outcome"] == "failure"
    assert chunk.rows(s.escalations) == 0 and chunk.artifact_rows() == []


def test_a_later_attempt_appends_its_own_artifacts_and_keeps_the_earlier(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    assert chunk.complete("unfulfillable", artifacts=_ARTIFACTS)["outcome"] == _PARKED
    chunk.hub.clock.advance(timedelta(seconds=1))
    assert chunk.hub.client.post(f"/api/chunks/{chunk.chunk_id}/requeues").status_code == 202
    claim = chunk.hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk.chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    report_lease(chunk.hub, chunk.chunk_id, epoch=2, seq=2)

    again = [{"name": "retry-notes", "kind": "asset", "content": "second try"}]
    assert chunk.complete("unfulfillable", epoch=2, artifacts=again)["outcome"] == _PARKED

    assert [(n, e) for n, _, e, _ in chunk.artifact_rows()] == [
        ("reasoning", 1),
        ("notes", 1),
        ("work", 1),
        ("retry-notes", 2),
    ]


def test_a_failing_artifact_insert_leaves_no_escalation_and_no_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    since = chunk.hub.events.latest_id()

    def boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("rollback")

    monkeypatch.setattr(chunk_escalations_store, "next_artifact_seq", boom)
    with pytest.raises(RuntimeError, match="rollback"):
        chunk.hub.client.post(
            f"/api/fleet/chunks/{chunk.chunk_id}/completions",
            json={
                "choice": "unfulfillable",
                "epoch": 1,
                "runner_id": "r1",
                "from_node_id": chunk.node_id,
                "artifacts": _ARTIFACTS,
            },
        )

    assert chunk.rows(s.escalations) == 0 and chunk.artifact_rows() == []
    assert chunk.detail()["status"] != "needs_human"
    assert emitted_events(chunk.hub, since=since) == []


def test_a_failing_escalation_insert_leaves_no_artifacts(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))

    def fail_on_escalation(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO ESCALATIONS"):
            raise RuntimeError("rollback")

    event.listen(chunk.hub.engine, "before_cursor_execute", fail_on_escalation)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            chunk.hub.client.post(
                f"/api/fleet/chunks/{chunk.chunk_id}/completions",
                json={
                    "choice": "unfulfillable",
                    "epoch": 1,
                    "runner_id": "r1",
                    "from_node_id": chunk.node_id,
                    "artifacts": _ARTIFACTS,
                },
            )
    finally:
        event.remove(chunk.hub.engine, "before_cursor_execute", fail_on_escalation)

    assert chunk.rows(s.escalations) == 0 and chunk.artifact_rows() == []


def test_a_displaced_claimant_escalate_choice_writes_neither_escalation_nor_artifacts(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    resp = chunk.hub.client.post(
        f"/api/fleet/chunks/{chunk.chunk_id}/completions",
        json={
            "choice": "unfulfillable",
            "epoch": 1,
            "runner_id": "r2",
            "from_node_id": chunk.node_id,
            "artifacts": _ARTIFACTS,
        },
    )

    assert resp.status_code == 200 and resp.json()["outcome"] == "failure"
    assert chunk.rows(s.escalations) == 0 and chunk.artifact_rows() == []


def test_a_gate_resolved_escalate_choice_keeps_the_decisions_artifacts_exactly_once(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    decided = chunk.hub.client.post(
        f"/api/fleet/chunks/{chunk.chunk_id}/decisions",
        json={"from_node_id": chunk.node_id, "epoch": 1, "runner_id": "r1", "artifacts": _ARTIFACTS},
    )
    assert decided.status_code == 200, decided.text
    decision_id = chunk.detail()["decision"]["decision_id"]
    resolved = chunk.hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "unfulfillable"})
    assert resolved.status_code == 200, resolved.text
    before = chunk.artifact_rows()
    assert [n for n, *_ in before] == ["reasoning", "notes", "work"]

    other = [{"name": "extra", "kind": "asset", "content": "x"}]
    assert chunk.complete("unfulfillable", decision_id=decision_id, artifacts=other)["outcome"] == _PARKED

    assert chunk.artifact_rows() == before
