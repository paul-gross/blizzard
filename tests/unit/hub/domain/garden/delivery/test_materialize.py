"""``GardenDelivery``: builds a ``DeliveryPlan`` from a
Phase-2 ``ValidatedDelivery`` over a fake repository — every id mints with the right
prefix, every field maps through from the wire ops, an empty delta still yields exactly
one finding_set entry and zero finding/fact entries (``bzh:domain-core``, the
``tests/unit/hub/domain/garden/proposals/test_model.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, WorkRef
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    GardenDelivery,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.domain.garden.delivery.validation import ValidatedDelivery
from blizzard.hub.domain.garden.findings.model import finding_lock
from blizzard.hub.domain.garden.formats import (
    DeliveredDelta,
    FindingAddOp,
    FindingGoneOp,
    FindingObservedOp,
    ProposalCandidate,
)
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from tests.support.fake_chunk_facts import FakeStatusFacts
from tests.support.fake_transactions import FakeTransactions
from tests.support.garden_artifacts import add_op, candidate

_PROJECT = "proj_test"


_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_RUN = RunContext(project_id=_PROJECT, routine_name="nightly", scope_slug="runner", mode="full")

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
)


@dataclass
class _FakeGardenDeliveryRepo:
    delivered: list[DeliveryPlan] = field(default_factory=list[DeliveryPlan])
    outcome: DeliveryOutcome = DeliveryOutcome.RECORDED

    terminals: list[bool] = field(default_factory=list[bool])
    written_in: list[LockedTransaction] = field(default_factory=list[LockedTransaction])

    def deliver(
        self, plan: DeliveryPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> DeliveryOutcome:
        self.delivered.append(plan)
        self.terminals.append(terminal)
        self.written_in.append(transaction)
        return self.outcome

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeGardenDeliveryRepo) -> IWriteGardenDeliveryRepository:
    return cast(IWriteGardenDeliveryRepository, repo)


def _fin() -> str:
    return Id.mint_at(IdPrefix.FINDING, _T0).value


def _add(
    *, locus: str = "a.py:1", summary: str = "s", introduced: str | None = None, ref: str | None = None
) -> FindingAddOp:
    payload: dict[str, object] = {"op": "add", "class": "stale-docstring", "locus": locus, "summary": summary}
    if introduced is not None:
        payload["introduced"] = introduced
    if ref is not None:
        payload["ref"] = ref
    return add_op(payload)


def _proposal(*, findings: list[str]) -> ProposalCandidate:
    return candidate({"ref": "p1", "class": "remediate", "title": "t", "body": "b", "findings": findings})


def test_deliver_builds_a_finding_and_its_add_fact_from_an_add_op() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add(introduced="b" * 40)])
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
    assert finding.finding_id.startswith(f"{IdPrefix.FINDING}_")
    assert finding.routine_name == "nightly"
    assert finding.scope_slug == "runner"
    assert finding.class_ == "stale-docstring"
    assert finding.locus == "a.py:1"
    assert finding.summary == "s"
    assert finding.introduced == "b" * 40
    assert finding.introduced_at is None  # no resolution supplied on `validated`

    assert [(f.finding_id, f.kind, f.note) for f in delta_materialization.facts] == [(finding.finding_id, "add", None)]

    fset = delta_materialization.finding_set
    assert fset.finding_set_id.startswith(f"{IdPrefix.FINDING_SET}_")
    # The fact attributes to the very set it was delivered under.
    assert delta_materialization.facts[0].finding_set_id == fset.finding_set_id
    assert fset.artifact_id == "art_1"
    assert fset.scope_slug == "runner"
    assert fset.revisions == {"blizzard": "a" * 40}
    assert fset.measurement is None

    assert plan.proposals == []


def test_deliver_threads_a_resolved_introduced_at_onto_the_new_finding() -> None:
    """`validated.introduced_at` is keyed `(repo, sha)` — the same pair `check_delta`
    resolved during validation — never re-resolved here."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    commit_at = datetime(2025, 12, 25, tzinfo=UTC)
    delta = DeliveredDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add(introduced="b" * 40)])
    validated = ValidatedDelivery(
        run=_RUN, deltas=[delta], proposals=[], introduced_at={("blizzard", "b" * 40): commit_at}
    )

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    finding = repo.delivered[0].deltas[0].new_findings[0]
    assert finding.introduced_at == commit_at


def test_deliver_leaves_introduced_at_none_when_the_resolved_pair_is_absent() -> None:
    """A missing entry (never resolved — no forge, ambiguous repo count) reads as
    unattributed, never a `KeyError`."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add(introduced="b" * 40)])
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[], introduced_at={})

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    finding = repo.delivered[0].deltas[0].new_findings[0]
    assert finding.introduced_at is None


def test_deliver_builds_observed_and_gone_facts_carrying_the_gone_note() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(
        scope="runner",
        findings=[FindingObservedOp(id="fin_1"), FindingGoneOp(id="fin_2", note="fixed upstream")],
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


def test_deliver_settles_a_gone_op_against_a_delivered_finding_to_resolved() -> None:
    """A `gone` op naming a finding the bucket carries as `delivered`
    completes that exit rather than merely flagging it — the settling fact carries the
    actor of the `delivered` fact it confirms, never `None`."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", findings=[FindingGoneOp(id="fin_1", note="no longer reproduces")])
    validated = ValidatedDelivery(
        run=_RUN, deltas=[delta], proposals=[], gone_settlements={"fin_1": ("resolved", "u_1")}
    )

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    facts = repo.delivered[0].deltas[0].facts
    assert [(f.finding_id, f.kind, f.note, f.actor) for f in facts] == [
        ("fin_1", "resolved", "no longer reproduces", "u_1")
    ]


def test_deliver_a_gone_op_against_a_finding_absent_from_gone_settlements_stays_gone() -> None:
    """A `gone` op targeting a finding `validated.gone_settlements` does not name — the
    ordinary case, a merely-live finding the run could not find — records the flag as
    before, carrying no actor."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", findings=[FindingGoneOp(id="fin_1", note="no longer reproduces")])
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    facts = repo.delivered[0].deltas[0].facts
    assert [(f.finding_id, f.kind, f.note, f.actor) for f in facts] == [("fin_1", "gone", "no longer reproduces", None)]


def test_deliver_settles_a_gone_op_against_an_actor_less_delivered_finding_too() -> None:
    """The finding is present in `gone_settlements` settling to `resolved` with no actor — membership
    gates the settlement, not the actor being truthy, so this must still complete the
    exit rather than falling through to a plain `gone` flag."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", findings=[FindingGoneOp(id="fin_1", note="no longer reproduces")])
    validated = ValidatedDelivery(
        run=_RUN, deltas=[delta], proposals=[], gone_settlements={"fin_1": ("resolved", None)}
    )

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    facts = repo.delivered[0].deltas[0].facts
    assert [(f.finding_id, f.kind, f.note, f.actor) for f in facts] == [
        ("fin_1", "resolved", "no longer reproduces", None)
    ]


def test_deliver_gives_a_citable_finding_the_delta_leaves_unnamed_no_fact() -> None:
    """`gone_settlements` spans every citable finding in the bucket, but a finding the run's
    delta does not name keeps its last word — only the ops the delta carries become facts."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", findings=[FindingObservedOp(id="fin_1")])
    validated = ValidatedDelivery(
        run=_RUN,
        deltas=[delta],
        proposals=[],
        gone_settlements={"fin_1": ("resolved", "u_1"), "fin_2": ("resolved", "u_1")},
    )

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=["art_1"])

    facts = repo.delivered[0].deltas[0].facts
    assert [(f.finding_id, f.kind) for f in facts] == [("fin_1", "observed")]


def test_deliver_on_an_empty_delta_yields_one_finding_set_and_no_findings_or_facts() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", revisions={}, measurement="12 findings", findings=[])
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
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta_a = DeliveredDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add(locus="a.py:1")])
    delta_b = DeliveredDelta(
        scope="runner",
        revisions={"blizzard": "b" * 40},
        findings=[FindingObservedOp(id="fin_1"), _add(locus="b.py:2")],
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
    assert {f.finding_set_id for f in materialized_a.facts} == {materialized_a.finding_set.finding_set_id}
    assert {f.finding_set_id for f in materialized_b.facts} == {materialized_b.finding_set.finding_set_id}
    assert materialized_a.finding_set.finding_set_id != materialized_b.finding_set.finding_set_id


def test_deliver_builds_a_proposal_and_its_finding_links() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    fin_1, fin_2 = _fin(), _fin()
    proposal = _proposal(findings=[fin_1, fin_2])
    validated = ValidatedDelivery(run=_RUN, deltas=[], proposals=[proposal], proposal_sources=["docket"])

    service.deliver(
        validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=[], proposal_artifact_ids=["art_p"]
    )

    plan = repo.delivered[0]
    assert plan.deltas == []
    assert len(plan.proposals) == 1
    built = plan.proposals[0]
    assert built.proposal_id.startswith(f"{IdPrefix.GARDEN_PROPOSAL}_")
    assert built.routine_name == "nightly"
    assert built.class_ == "remediate"
    assert built.title == "t"
    assert built.body == "b"
    assert built.source_artifact_id == "art_p"
    assert built.ref == "p1"
    assert built.finding_ids == [fin_1, fin_2]


def test_deliver_resolves_a_proposals_ref_citation_against_the_id_its_own_add_op_minted() -> None:
    """The `ref -> finding_id` map is built in the same walk that mints each `add` op's
    id — a proposal citing that `ref` lands with the actual minted id in its link
    rows, never the submission-local spelling."""
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", findings=[_add(locus="a.py:1", ref="new-1")])
    proposal = _proposal(findings=["new-1"])
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[proposal], proposal_sources=["docket"])

    service.deliver(
        validated,
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_1"],
        proposal_artifact_ids=["art_p"],
    )

    plan = repo.delivered[0]
    minted_finding_id = plan.deltas[0].new_findings[0].finding_id
    assert plan.proposals[0].finding_ids == [minted_finding_id]
    assert minted_finding_id != "new-1"


def test_deliver_resolves_a_proposal_mixing_a_ref_and_a_prior_runs_live_id() -> None:
    repo = _FakeGardenDeliveryRepo()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    prior_live = _fin()
    delta = DeliveredDelta(scope="runner", findings=[_add(locus="a.py:1", ref="new-1")])
    proposal = _proposal(findings=[prior_live, "new-1"])
    validated = ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[proposal], proposal_sources=["docket"])

    service.deliver(
        validated,
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_1"],
        proposal_artifact_ids=["art_p"],
    )

    plan = repo.delivered[0]
    minted_finding_id = plan.deltas[0].new_findings[0].finding_id
    assert plan.proposals[0].finding_ids == [prior_live, minted_finding_id]


def test_deliver_returns_the_repository_outcome() -> None:
    repo = _FakeGardenDeliveryRepo(outcome=DeliveryOutcome.ALREADY_RECORDED)
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    validated = ValidatedDelivery(run=_RUN, deltas=[], proposals=[])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1, delta_artifact_ids=[])

    assert outcome is DeliveryOutcome.ALREADY_RECORDED


def test_deliver_writes_in_a_unit_of_its_own_that_locks_the_chunk_and_every_expected_finding() -> None:
    repo = _FakeGardenDeliveryRepo()
    transactions = FakeTransactions()
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=transactions,
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(
        scope="runner",
        revisions={"blizzard": "a" * 40},
        findings=[_add(), FindingObservedOp(id="fin_2"), FindingGoneOp(id="fin_1", note="fixed")],
    )

    service.deliver(
        ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[], expect={"fin_1": "live", "fin_2": "live"}),
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_1"],
    )

    assert transactions.locked_chunks == [[_CHUNK.chunk_id]]
    assert transactions.locked_keys == [[finding_lock("fin_1"), finding_lock("fin_2")]]


@pytest.mark.parametrize(
    ("facts", "terminal"),
    [
        ({}, False),
        ({_CHUNK.chunk_id: ChunkFacts(minted=True)}, False),
        ({_CHUNK.chunk_id: ChunkFacts(minted=True, stopped=True, stopped_at=_T0)}, True),
    ],
    ids=["unknown", "live", "stopped"],
)
def test_deliver_hands_the_write_the_terminal_verdict_read_under_its_lock(
    facts: dict[str, ChunkFacts], terminal: bool
) -> None:
    repo = _FakeGardenDeliveryRepo()
    status_facts = FakeStatusFacts(facts)
    service = GardenDelivery(
        delivery=_as_write_repo(repo),
        facts=status_facts.as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    delta = DeliveredDelta(scope="runner", revisions={"blizzard": "a" * 40}, findings=[_add()])

    service.deliver(
        ValidatedDelivery(run=_RUN, deltas=[delta], proposals=[]),
        chunk=_CHUNK,
        node=_NODE,
        epoch=1,
        delta_artifact_ids=["art_1"],
    )

    assert repo.terminals == [terminal]
    assert status_facts.transactions == repo.written_in
