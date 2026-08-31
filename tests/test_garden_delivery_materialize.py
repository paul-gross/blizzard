"""``GardenDelivery`` (unit tier, blizzard#393 Phase 3): builds a ``DeliveryPlan`` from a
Phase-2 ``ValidatedDelivery`` over a fake repository — every id mints with the right
prefix, every field maps through from the wire ops, an empty delta still yields exactly
one finding_set entry and zero finding/fact entries (``bzh:domain-core``, the
``tests/test_garden_proposals_domain.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import FINDING_PREFIX, FINDING_SET_PREFIX, GARDEN_PROPOSAL_PREFIX
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.garden_delivery import ValidatedDelivery
from blizzard.hub.domain.garden_delivery_materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    GardenDelivery,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.domain.graph import Node
from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import Chunk, WorkRef
from blizzard.wire.finding import AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp
from blizzard.wire.garden_proposal import GardenProposalCandidate

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_RUN = RunContext(routine_name="nightly", scope_slug="runner", mode="full")

_CHUNK = Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=[WorkRef(source="default", ref="1")], minted_at=_T0)
_NODE = Node(
    node_id="nd_1",
    graph_id="gr_1",
    name="garden-survey",
    executor=Executor.HUB,
    prompt=None,
    checks=[],
    produces=[],
    session=SessionMode.FRESH,
    judged_by=JudgedBy.WORKER,
    retries_max=None,
    retries_exhausted=None,
    mode=None,
)


@dataclass
class _FakeGardenDeliveryRepo:
    delivered: list[DeliveryPlan] = field(default_factory=list)
    outcome: DeliveryOutcome = DeliveryOutcome.RECORDED

    def deliver(self, plan: DeliveryPlan) -> DeliveryOutcome:
        self.delivered.append(plan)
        return self.outcome

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeGardenDeliveryRepo) -> IWriteGardenDeliveryRepository:
    return cast(IWriteGardenDeliveryRepository, repo)


def _add(*, locus: str = "a.py:1", summary: str = "s", introduced: str | None = None) -> AddFindingOp:
    payload: dict[str, object] = {"op": "add", "class": "stale-docstring", "locus": locus, "summary": summary}
    if introduced is not None:
        payload["introduced"] = introduced
    return AddFindingOp.model_validate(payload)


def _proposal(*, findings: list[str]) -> GardenProposalCandidate:
    return GardenProposalCandidate.model_validate(
        {"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": findings}
    )


def test_deliver_builds_a_finding_and_its_add_fact_from_an_add_op() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    delta = FindingDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add(introduced="b" * 40)])
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    assert outcome is DeliveryOutcome.RECORDED
    assert len(repo.delivered) == 1
    plan = repo.delivered[0]
    assert plan.chunk_id == "ch_1"
    assert plan.node_id == "nd_1"
    assert plan.node_name == "garden-survey"
    assert plan.epoch == 1
    assert plan.at == _T0
    assert plan.run == _RUN

    assert len(plan.deltas) == 1
    delta_materialization = plan.deltas[0]

    assert len(delta_materialization.new_findings) == 1
    finding = delta_materialization.new_findings[0]
    assert finding.finding_id.startswith(f"{FINDING_PREFIX}_")
    assert finding.routine_name == "nightly"
    assert finding.scope_slug == "runner"
    assert finding.class_ == "stale-docstring"
    assert finding.locus == "a.py:1"
    assert finding.summary == "s"
    assert finding.introduced == "b" * 40

    assert [(f.finding_id, f.kind, f.note) for f in delta_materialization.facts] == [(finding.finding_id, "add", None)]

    fset = delta_materialization.finding_set
    assert fset.finding_set_id.startswith(f"{FINDING_SET_PREFIX}_")
    assert fset.artifact_id == "art_1"
    assert fset.scope_slug == "runner"
    assert fset.revisions == {"blizzard": "a" * 40}
    assert fset.measurement is None

    assert plan.proposals == []


def test_deliver_builds_observed_and_gone_facts_carrying_the_gone_note() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    delta = FindingDelta(
        scope="runner",
        findings=[ObservedFindingOp(id="fin_1"), GoneFindingOp(id="fin_2", note="fixed upstream")],
    )
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    plan = repo.delivered[0]
    delta_materialization = plan.deltas[0]
    assert delta_materialization.new_findings == []
    assert [(f.finding_id, f.kind, f.note) for f in delta_materialization.facts] == [
        ("fin_1", "observed", None),
        ("fin_2", "gone", "fixed upstream"),
    ]


def test_deliver_on_an_empty_delta_yields_one_finding_set_and_no_findings_or_facts() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    delta = FindingDelta(scope="runner", revisions={}, measurement="12 findings", findings=[])
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    plan = repo.delivered[0]
    assert len(plan.deltas) == 1
    delta_materialization = plan.deltas[0]
    assert delta_materialization.new_findings == []
    assert delta_materialization.facts == []
    assert delta_materialization.finding_set.artifact_id == "art_1"
    assert delta_materialization.finding_set.measurement == "12 findings"


def test_deliver_over_two_deltas_groups_each_deltas_own_rows_separately() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    delta_a = FindingDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add(locus="a.py:1")])
    delta_b = FindingDelta(
        scope="runner",
        revisions={"blizzard": "b" * 40},
        findings=[ObservedFindingOp(id="fin_1"), _add(locus="b.py:2")],
    )
    validated = ValidatedDelivery(run=_RUN, deltas=[delta_a, delta_b], proposals=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_a", "art_b"])

    plan = repo.delivered[0]
    assert len(plan.deltas) == 2
    materialized_a, materialized_b = plan.deltas

    assert materialized_a.finding_set.artifact_id == "art_a"
    assert len(materialized_a.new_findings) == 1
    assert materialized_a.new_findings[0].locus == "a.py:1"
    assert [(f.kind) for f in materialized_a.facts] == ["add"]

    assert materialized_b.finding_set.artifact_id == "art_b"
    assert len(materialized_b.new_findings) == 1
    assert materialized_b.new_findings[0].locus == "b.py:2"
    assert [f.kind for f in materialized_b.facts] == ["observed", "add"]

    # Each group carries only its own delta's rows — no cross-contamination.
    assert materialized_a.new_findings[0].finding_id != materialized_b.new_findings[0].finding_id


def test_deliver_builds_a_proposal_and_its_finding_links() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    proposal = _proposal(findings=["fin_1", "fin_2"])
    validated = ValidatedDelivery(run=_RUN, deltas=[], proposals=[proposal])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=[])

    plan = repo.delivered[0]
    assert plan.deltas == []
    assert len(plan.proposals) == 1
    built = plan.proposals[0]
    assert built.proposal_id.startswith(f"{GARDEN_PROPOSAL_PREFIX}_")
    assert built.routine_name == "nightly"
    assert built.class_ == "remediate"
    assert built.title == "t"
    assert built.body == "b"
    assert built.finding_ids == ["fin_1", "fin_2"]


def test_deliver_returns_the_repository_outcome() -> None:
    repo = _FakeGardenDeliveryRepo(outcome=DeliveryOutcome.ALREADY_RECORDED)
    service = GardenDelivery(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    validated = ValidatedDelivery(run=_RUN, deltas=[], proposals=[])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=[])

    assert outcome is DeliveryOutcome.ALREADY_RECORDED
