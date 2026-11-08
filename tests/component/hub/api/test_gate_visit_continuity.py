"""A gate decision belongs to its node visit, not to the claim or lease that opened it — component tier.

Every case drives real stored decisions through the production HTTP and store paths: the current
holder, whether a new claimant after a detach or the same route at a later lease, carries the visit's
decision at its own fence; a plain exit and a competing fresh gate are refused while the visit holds an
unconsumed decision; the worker-facing status read reports that visit's decision and no other; and an
exit whose plan raced a gate opening at the plan→lock boundary is refused under the lock, writing nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.execution.apply import ApplyService
from blizzard.hub.store import schema as s
from tests.support.scaffolding import HubHarness, build_hub, chunk_rows, hub_scoped, ingest, report_lease

# build (worker) gated by runner config; its exits cover a transition, an escalation and a migration.
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
          to: deliver
        fail:
          description: Incomplete.
          to: build
        blocked:
          description: Blocked on something outside the repository.
          to: escalate
        hand-off:
          description: Hand off to another graph.
          to: graph:triage
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

_TRIAGE_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Triage.
    judgement:
      prompt: |
        Assess.
      choices:
        pass:
          description: Done.
          to: done
"""

_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}


class _Chunk:
    """One chunk at ``build``, posting as whichever runner holds it."""

    def __init__(self, hub: HubHarness) -> None:
        self.hub = hub
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
        assert graph.status_code == 201, graph.text
        self.build = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
        triage = hub.client.post("/api/graphs", json={"definition_yaml": _TRIAGE_YAML})
        assert triage.status_code == 201, triage.text
        self.chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        self.seq = 0

    def claim(self, runner_id: str, *, epoch: int) -> None:
        resp = self.hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": self.chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert resp.status_code == 201, resp.text
        self.lease(runner_id, epoch=epoch)

    def lease(self, runner_id: str, *, epoch: int) -> None:
        self.seq += 1
        report_lease(self.hub, self.chunk_id, epoch=epoch, seq=self.seq, runner_id=runner_id)

    def gate(self, runner_id: str, *, epoch: int) -> dict[str, Any]:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{self.chunk_id}/decisions",
            json={"from_node_id": self.build, "epoch": epoch, "runner_id": runner_id, "artifacts": [_ARTIFACT]},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def complete(self, runner_id: str, *, choice: str, epoch: int, decision_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": runner_id,
            "from_node_id": self.build,
            "artifacts": [] if decision_id is not None else [_ARTIFACT],
        }
        if decision_id is not None:
            body["decision_id"] = decision_id
        resp = self.hub.client.post(f"/api/fleet/chunks/{self.chunk_id}/completions", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def resolve(self, decision_id: str, choice: str) -> None:
        resp = self.hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": choice})
        assert resp.status_code == 200, resp.text

    def detach(self) -> None:
        resp = self.hub.client.post(f"/api/chunks/{self.chunk_id}/detach")
        assert resp.status_code == 202, resp.text

    def detail(self) -> dict[str, Any]:
        return self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()

    def status_decision(self) -> dict[str, Any] | None:
        resp = self.hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": [self.chunk_id]})
        assert resp.status_code == 200, resp.text
        [view] = resp.json()
        return view["decision"]

    def rows(self, table: sa.Table) -> int:
        return chunk_rows(self.hub, table, self.chunk_id)

    def decision_ids(self) -> list[str]:
        with self.hub.engine.begin() as conn:
            return list(
                conn.execute(
                    hub_scoped(
                        sa.select(s.decisions.c.decision_id)
                        .where(s.decisions.c.chunk_id == self.chunk_id)
                        .order_by(s.decisions.c.submitted_at)
                    )
                ).scalars()
            )

    def transition_decisions(self) -> list[str | None]:
        with self.hub.engine.begin() as conn:
            return list(
                conn.execute(
                    hub_scoped(sa.select(s.transitions.c.decision_id).where(s.transitions.c.chunk_id == self.chunk_id))
                ).scalars()
            )


def _resolved_then_reclaimed(hub: HubHarness, choice: str = "pass") -> tuple[_Chunk, str]:
    """r1 gates ``build`` at epoch 1 with one artifact, a person resolves it to ``choice``, the chunk
    is detached, and r2 claims it at epoch 2. Returns the chunk and the decision id."""
    chunk = _Chunk(hub)
    chunk.claim("r1", epoch=1)
    assert chunk.gate("r1", epoch=1)["outcome"] == "parked_at_gate"
    [decision_id] = chunk.decision_ids()
    chunk.resolve(decision_id, choice)
    chunk.detach()
    assert chunk.detail()["status"] == "ready"
    chunk.claim("r2", epoch=2)
    return chunk, decision_id


# --- the current holder carries the visit's decision ----------------------------------------------


def test_a_new_claimant_carries_the_resolved_decision_at_its_own_fence(tmp_path: Path) -> None:
    chunk, decision_id = _resolved_then_reclaimed(build_hub(tmp_path))
    artifacts = chunk.rows(s.artifacts)

    resolving = chunk.complete("r2", choice="pass", epoch=2, decision_id=decision_id)

    assert resolving["outcome"] == "hub_node_taken", resolving
    assert chunk.transition_decisions() == [decision_id]
    assert chunk.decision_ids() == [decision_id]
    assert chunk.rows(s.artifacts) == artifacts


def test_the_same_route_at_a_later_lease_carries_the_open_decision_once_resolved(tmp_path: Path) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.claim("r1", epoch=1)
    chunk.gate("r1", epoch=1)
    [decision_id] = chunk.decision_ids()
    chunk.lease("r1", epoch=2)

    plain = chunk.complete("r1", choice="pass", epoch=2)
    assert plain["outcome"] == "failure" and f"decision {decision_id} is open" in plain["detail"]

    chunk.resolve(decision_id, "fail")
    resolving = chunk.complete("r1", choice="fail", epoch=2, decision_id=decision_id)

    assert resolving["outcome"] == "next", resolving
    assert chunk.transition_decisions() == [decision_id]


def test_a_new_claimant_naming_a_decision_the_visit_does_not_hold_is_refused(tmp_path: Path) -> None:
    chunk, decision_id = _resolved_then_reclaimed(build_hub(tmp_path), choice="fail")
    assert chunk.complete("r2", choice="fail", epoch=2, decision_id=decision_id)["outcome"] == "next"
    # The chunk re-entered `build` at epoch 2: the closed decision gives no authority for a fresh exit.
    chunk.lease("r2", epoch=3)

    stale = chunk.complete("r2", choice="fail", epoch=3, decision_id=decision_id)

    assert stale["outcome"] == "failure" and "is not the current visit's decision" in stale["detail"]
    assert chunk.transition_decisions() == [decision_id]


# --- a plain exit and a competing gate are refused ------------------------------------------------


@pytest.mark.parametrize("choice", ["pass", "blocked", "hand-off"])
def test_a_new_claimants_plain_exit_is_refused_while_the_visit_holds_the_resolved_decision(
    tmp_path: Path, choice: str
) -> None:
    chunk, decision_id = _resolved_then_reclaimed(build_hub(tmp_path))
    artifacts = chunk.rows(s.artifacts)

    plain = chunk.complete("r2", choice=choice, epoch=2)

    assert plain["outcome"] == "failure"
    assert f"decision {decision_id} at node `build` is resolved to `pass`" in plain["detail"]
    assert (chunk.rows(s.transitions), chunk.rows(s.escalations), chunk.rows(s.chunk_migrations)) == (0, 0, 0)
    assert chunk.rows(s.artifacts) == artifacts


def test_a_competing_fresh_gate_in_the_visit_is_refused_and_appends_nothing(tmp_path: Path) -> None:
    chunk, decision_id = _resolved_then_reclaimed(build_hub(tmp_path))
    artifacts = chunk.rows(s.artifacts)

    second = chunk.gate("r2", epoch=2)

    assert second["outcome"] == "failure" and f"decision {decision_id}" in second["detail"]
    assert chunk.decision_ids() == [decision_id]
    assert chunk.rows(s.artifacts) == artifacts
    # The exact (chunk, node, epoch) replay stays a parked no-op.
    assert chunk.gate("r1", epoch=1)["outcome"] in {"parked_at_gate", "failure"}
    assert chunk.decision_ids() == [decision_id]


# --- the worker-facing status read reports the visit's decision -----------------------------------


def test_the_status_read_reports_the_visit_decision_to_a_new_claimant(tmp_path: Path) -> None:
    chunk, decision_id = _resolved_then_reclaimed(build_hub(tmp_path))

    decision = chunk.status_decision()

    assert decision is not None
    assert (decision["decision_id"], decision["node_id"], decision["resolved_choice"]) == (
        decision_id,
        chunk.build,
        "pass",
    )


def test_the_status_read_ignores_an_earlier_visits_unconsumed_decision(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _Chunk(hub)
    chunk.claim("r1", epoch=1)
    assert chunk.complete("r1", choice="fail", epoch=1)["outcome"] == "next"
    chunk.lease("r1", epoch=2)
    assert chunk.complete("r1", choice="fail", epoch=2)["outcome"] == "next"
    # A historical duplicate left unconsumed by the visit that arrived at epoch 1.
    with hub.engine.begin() as conn:
        owner = conn.execute(
            hub_scoped(
                sa.select(s.chunks.c.project_id, s.chunks.c.tenant_id).where(s.chunks.c.chunk_id == chunk.chunk_id)
            )
        ).one()
        conn.execute(
            hub_scoped(
                sa.insert(s.decisions).values(
                    decision_id="dec_stale",
                    chunk_id=chunk.chunk_id,
                    project_id=owner.project_id,
                    tenant_id=owner.tenant_id,
                    node_id=chunk.build,
                    node_name="build",
                    epoch=1,
                    choices="[]",
                    submitted_at=hub.clock.now() + timedelta(minutes=1),
                    imposed_by_runner_id="r1",
                )
            )
        )

    assert chunk.status_decision() is None
    # The current visit holds nothing: a plain exit proceeds.
    chunk.lease("r1", epoch=3)
    assert chunk.complete("r1", choice="pass", epoch=3)["outcome"] == "hub_node_taken"


# --- the plan→lock boundary -----------------------------------------------------------------------


def _at_lock_boundary(monkeypatch: pytest.MonkeyPatch, hook: str, competing: Callable[[], object]) -> list[object]:
    """Run ``competing`` once the exit's plan is decided and before ``hook`` takes the chunk's lock."""
    original: Callable[..., Any] = getattr(ApplyService, hook)
    ran: list[object] = []

    def interleave(self: ApplyService, *args: Any, **kwargs: Any) -> Any:
        if not ran:
            ran.append(competing())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ApplyService, hook, interleave)
    return ran


@pytest.mark.parametrize(
    ("choice", "hook"),
    [("pass", "_transition"), ("blocked", "_escalate_choice"), ("hand-off", "_land_migration")],
)
def test_a_gate_opened_at_the_plan_to_lock_boundary_refuses_the_exit_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str, hook: str
) -> None:
    chunk = _Chunk(build_hub(tmp_path))
    chunk.claim("r1", epoch=1)
    ran = _at_lock_boundary(monkeypatch, hook, lambda: chunk.gate("r1", epoch=1))

    plain = chunk.complete("r1", choice=choice, epoch=1)

    assert [r["outcome"] for r in ran if isinstance(r, dict)] == ["parked_at_gate"]
    [decision_id] = chunk.decision_ids()
    assert plain["outcome"] == "failure" and f"decision {decision_id} is open" in plain["detail"]
    assert (chunk.rows(s.transitions), chunk.rows(s.escalations), chunk.rows(s.chunk_migrations)) == (0, 0, 0)
    assert chunk.detail()["status"] == "waiting_on_human"


def test_a_resolving_re_escalation_replay_still_must_name_this_nodes_decision(tmp_path: Path) -> None:
    chunk, decision_id = _resolved_then_reclaimed(build_hub(tmp_path), choice="blocked")
    first = chunk.complete("r2", choice="blocked", epoch=2, decision_id=decision_id)
    assert first["outcome"] != "failure", first

    replay = chunk.complete("r2", choice="blocked", epoch=2, decision_id=decision_id)
    assert replay["outcome"] != "failure", replay

    foreign = chunk.complete("r2", choice="blocked", epoch=2, decision_id="dec_not_this_nodes")
    assert foreign["outcome"] == "failure" and "does not match node" in foreign["detail"]
