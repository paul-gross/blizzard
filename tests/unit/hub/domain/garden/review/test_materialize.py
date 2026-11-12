"""``ReviewFindingsMaterialize``: builds a
``ReviewFindingsPlan`` from a ``ValidatedReviewFindings`` over a fake repository — every
id mints with the right prefix, every field maps through from the wire entry, an empty
delta yields an empty plan (the ``tests/unit/hub/domain/garden/delivery/test_materialize.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import IdPrefix
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, WorkRef
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.formats import DeferredReviewEntry
from blizzard.hub.domain.garden.review.materialize import (
    IWriteReviewFindingsRepository,
    ReviewFindingsMaterialize,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.domain.garden.review.validation import ValidatedReviewFindings
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from tests.support.fake_chunk_facts import FakeStatusFacts
from tests.support.fake_transactions import FakeTransactions
from tests.support.garden_artifacts import deferred_entry

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_CHUNK = Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=[WorkRef(source="default", ref="1")], minted_at=_T0)
_NODE = Node(
    node_id="nd_1",
    graph_id="gr_1",
    name="record-findings",
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
class _FakeReviewFindingsRepo:
    delivered: list[ReviewFindingsPlan] = field(default_factory=list[ReviewFindingsPlan])
    outcome: ReviewFindingsOutcome = ReviewFindingsOutcome.RECORDED

    terminals: list[bool] = field(default_factory=list[bool])

    def deliver(
        self, plan: ReviewFindingsPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> ReviewFindingsOutcome:
        self.delivered.append(plan)
        self.terminals.append(terminal)
        return self.outcome

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeReviewFindingsRepo) -> IWriteReviewFindingsRepository:
    return cast(IWriteReviewFindingsRepository, repo)


def _deferred(ref: str = "F1", *, scope: str = "blizzard", severity: str = "should-fix") -> DeferredReviewEntry:
    return deferred_entry(
        {
            "ref": ref,
            "disposition": "deferred",
            "severity": severity,
            "scope": scope,
            "class": "correctness",
            "locus": "a.py:1",
            "summary": "s",
        }
    )


def test_deliver_builds_a_finding_and_its_add_fact_from_a_deferred_entry() -> None:
    repo = _FakeReviewFindingsRepo()
    service = ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    validated = ValidatedReviewFindings(deferred=[_deferred()])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    assert outcome is ReviewFindingsOutcome.RECORDED
    assert len(repo.delivered) == 1
    plan = repo.delivered[0]
    assert plan.chunk_id == "ch_1"
    assert plan.node_id == "nd_1"
    assert plan.node_name == "record-findings"
    assert plan.epoch == 1
    assert plan.at == _T0
    assert plan.new_scope_description == "Minted by review delivery on chunk ch_1"

    assert len(plan.new_findings) == 1
    finding = plan.new_findings[0]
    assert finding.finding_id.startswith(f"{IdPrefix.FINDING}_")
    assert finding.scope_slug == "blizzard"
    assert finding.class_ == "correctness"
    assert finding.locus == "a.py:1"
    assert finding.summary == "s"
    assert finding.severity == "should-fix"
    assert finding.raised_by_chunk_id == "ch_1"

    assert len(plan.facts) == 1
    assert plan.facts[0].finding_id == finding.finding_id
    assert plan.facts[0].ref == "F1"

    assert plan.scope_slugs == ["blizzard"]


def test_deliver_on_an_empty_delta_yields_an_empty_plan() -> None:
    repo = _FakeReviewFindingsRepo()
    service = ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    validated = ValidatedReviewFindings(deferred=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    plan = repo.delivered[0]
    assert plan.new_findings == []
    assert plan.facts == []
    assert plan.scope_slugs == []


def test_deliver_over_two_entries_mints_two_distinct_findings() -> None:
    repo = _FakeReviewFindingsRepo()
    service = ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    validated = ValidatedReviewFindings(deferred=[_deferred(ref="F1"), _deferred(ref="F2", scope="other")])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    plan = repo.delivered[0]
    assert len(plan.new_findings) == 2
    a, b = plan.new_findings
    assert a.finding_id != b.finding_id
    assert plan.scope_slugs == ["blizzard", "other"]


def test_deliver_returns_the_repository_outcome() -> None:
    repo = _FakeReviewFindingsRepo(outcome=ReviewFindingsOutcome.ALREADY_RECORDED)
    service = ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )
    validated = ValidatedReviewFindings(deferred=[])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    assert outcome is ReviewFindingsOutcome.ALREADY_RECORDED


def test_deliver_writes_in_a_unit_of_its_own_that_locks_the_chunk() -> None:
    repo = _FakeReviewFindingsRepo()
    transactions = FakeTransactions()
    service = ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts().as_port(),
        transactions=transactions,
        clock=FixedClock(_T0),
    )

    service.deliver(ValidatedReviewFindings(deferred=[]), chunk=_CHUNK, node=_NODE, epoch=1)

    assert transactions.locked_chunks == [[_CHUNK.chunk_id]]


@pytest.mark.parametrize(
    ("facts", "terminal"),
    [
        ({}, False),
        ({_CHUNK.chunk_id: ChunkFacts(minted=True)}, False),
        ({_CHUNK.chunk_id: ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)}, True),
    ],
    ids=["unknown", "live", "done"],
)
def test_deliver_hands_the_write_the_terminal_verdict_read_under_its_lock(
    facts: dict[str, ChunkFacts], terminal: bool
) -> None:
    repo = _FakeReviewFindingsRepo()
    service = ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        facts=FakeStatusFacts(facts).as_port(),
        transactions=FakeTransactions(),
        clock=FixedClock(_T0),
    )

    service.deliver(ValidatedReviewFindings(deferred=[]), chunk=_CHUNK, node=_NODE, epoch=1)

    assert repo.terminals == [terminal]
