"""Helpers for the flow driver tests, shared with other test modules."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Table, func, select

from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.flow_driver import FlowLineStores, TickResult
from blizzard.hub.domain.actions.effects import IEffectSink, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.delivery.flows import Submission
from blizzard.hub.domain.delivery.reconcile import (
    FlowLineReconciler,
)
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.forge.landing import VisitForges
from blizzard.hub.store import schema as s
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import (
    TEST_HUB_ACTIONS,
    build_hub,
    default_tenant,
    hub_scoped,
    pointer_token,
    report_lease,
)


class _HeldLanding:
    """A landing action answering ``answer`` — ``pending`` until told otherwise — among ``pending``, ``landed``,
    ``failure`` and any ``extra`` answers its graph authors."""

    def __init__(self, extra: frozenset[str] = frozenset()) -> None:
        self.spec = ActionSpec(
            name="test.held-landing",
            args=frozenset(),
            required_args=frozenset(),
            answers=frozenset({"pending", "landed", "failure"}) | extra,
            answers_from_arg=None,
            lands=True,
        )
        self.answer = Pass(choice="pending")
        self.passes: list[str] = []

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        self.passes.append(visit.chunk.chunk_id)
        return self.answer


_GRAPH = """
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
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    poll_interval: 30
    action: test.held-landing
    judgement:
      choices:
        landed:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""


_REPO = "acme/widget"


class World:
    def __init__(self, tmp_path: Path, *, extra_answers: frozenset[str] = frozenset()) -> None:
        self.action = _HeldLanding(extra_answers)
        actions = HubActions.of([*TEST_HUB_ACTIONS.implementations.values(), self.action])
        self.hub = build_hub(tmp_path, actions=actions)
        self.tenant = default_tenant(self.hub.engine)
        self.driver = self.hub.flow_driver
        self.stores = FlowLineStores(
            corpus=self.hub.hub_services.hub_scoped_reads.corpus,
            tenants=lambda: self.hub.hub_services.tenants.open_tenants.every().keys(),
            lines=self.lines,
            turns=lambda tenant: self.hub.open_services(tenant).turns,
            notices=lambda tenant: BudgetNoticeDrain(
                self.hub.open_services(tenant).forge_budgets, self.hub.open_services(tenant).event_log
            ),
            publishers=lambda tenant: self.hub.open_services(tenant).events,
        )

    def lines(self, tenant: TenantId) -> FlowLineReconciler:
        return self.hub.hub_services.tenants.open_services(StoreScope(tenant)).flow_lines

    def tick(self) -> TickResult:
        """One tick, its turns run to their end."""
        (result,) = self.hub.take_turns()
        return result

    def mint(self, graph_yaml: str = _GRAPH, ref: str = "42") -> tuple[str, str]:
        """A promoted, claimed chunk at ``build``, epoch 1: its id and ``build``'s node id."""
        hub = self.hub
        hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml})
        resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]})
        assert resp.status_code == 201, resp.text
        chunk_id = resp.json()["chunk_id"]
        hub.promote(chunk_id)
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": [f"env-{ref}"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, chunk_id, epoch=1, seq=1)
        return chunk_id, claim.json()["envelope"]["node"]["node_id"]

    def complete(self, chunk_id: str, from_node_id: str, *, repo: str = _REPO, epoch: int = 1) -> dict[str, Any]:
        resp = self.hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={
                "choice": "pass",
                "epoch": epoch,
                "runner_id": "r1",
                "from_node_id": from_node_id,
                "check_results": [],
                "artifacts": [
                    {"name": "w", "kind": "git_commit", "repo": repo, "branch_name": "b", "commit_hash": "a" * 40}
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def at_merge(self, graph_yaml: str = _GRAPH, ref: str = "42") -> str:
        """A chunk standing at ``merge``, not yet submitted."""
        chunk_id, build = self.mint(graph_yaml, ref)
        self.complete(chunk_id, build)
        assert self.detail(chunk_id)["status"] == "delivering"
        return chunk_id

    def detail(self, chunk_id: str) -> dict[str, Any]:
        return self.hub.client.get(f"/api/chunks/{chunk_id}").json()

    def submissions(self, chunk_id: str) -> list[Submission]:
        """Every submission of the chunk, open or closed, in arrival order."""
        flows = self.hub.services.flows
        with self.hub.engine.connect() as conn:
            rows = conn.execute(
                hub_scoped(
                    select(s.flow_submissions.c.node_id, s.flow_submissions.c.epoch)
                    .where(s.flow_submissions.c.chunk_id == chunk_id)
                    .order_by(s.flow_submissions.c.arrival)
                )
            ).all()
        found = [flows.submission_for_visit(chunk_id, node_id=r.node_id, epoch=r.epoch) for r in rows]
        return [sub for sub in found if sub is not None]

    def turn_outcomes(self, chunk_id: str) -> list[str | None]:
        """Every turn the chunk took, oldest first."""
        joined = s.hub_turns.join(s.flow_submissions, s.hub_turns.c.submission_id == s.flow_submissions.c.submission_id)
        with self.hub.engine.connect() as conn:
            return list(
                conn.execute(
                    hub_scoped(
                        select(s.hub_turns.c.outcome)
                        .select_from(joined)
                        .where(s.flow_submissions.c.chunk_id == chunk_id)
                        .order_by(s.hub_turns.c.turn_id)
                    )
                ).scalars()
            )

    def count(self, table: Table) -> int:
        with self.hub.engine.connect() as conn:
            return int(conn.execute(hub_scoped(select(func.count()).select_from(table))).scalar() or 0)

    def poll_due(self) -> None:
        """Past every waiting visit's poll interval."""
        self.hub.clock.advance(timedelta(seconds=31))
