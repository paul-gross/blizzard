"""``ReviewFindingsMaterialize``: builds a
``ReviewFindingsPlan`` from a ``ValidatedReviewFindings`` over a fake repository — every
id mints with the right prefix, every field maps through from the wire entry, an empty
delta yields an empty plan (the ``tests/unit/hub/domain/garden/delivery/test_materialize.py`` shape)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import IdPrefix
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, WorkRef
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.garden.findings.model import FindingFactInsert, FindingInsert, IWriteFindingRepository
from blizzard.hub.domain.garden.formats import DeferredReviewEntry
from blizzard.hub.domain.garden.review.materialize import (
    IWriteReviewFindingsRepository,
    ReviewAdmission,
    ReviewFindingsMaterialize,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.domain.garden.review.validation import ValidatedReviewFindings
from blizzard.hub.domain.garden.scopes import IWriteScopeRepository, Scope
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from tests.support.fake_chunk_facts import FakeStatusFacts
from tests.support.fake_transactions import FakeTransactions
from tests.support.garden_artifacts import deferred_entry

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_PROJECT = "proj_test"

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
    """The review-delivery, finding, and scope repositories at once, recording each write in order."""

    delivered: list[ReviewFindingsPlan] = field(default_factory=list[ReviewFindingsPlan])
    outcome: ReviewFindingsOutcome = ReviewFindingsOutcome.RECORDED

    terminals: list[bool] = field(default_factory=list[bool])
    written_in: list[LockedTransaction] = field(default_factory=list[LockedTransaction])
    writes: list[tuple[str, object]] = field(default_factory=list[tuple[str, object]])

    def admit(
        self, plan: ReviewFindingsPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> ReviewFindingsOutcome | ReviewAdmission:
        self.delivered.append(plan)
        self.terminals.append(terminal)
        self.written_in.append(transaction)
        if self.outcome is not ReviewFindingsOutcome.RECORDED:
            return self.outcome
        return ReviewAdmission(project_id=_PROJECT)

    def ensure(self, record: Scope, *, change: ConfigChange, transaction: Transaction | None = None) -> Scope:
        self.writes.append(("scope", (record.project_id, record.slug, transaction)))
        return record

    def insert_many(self, rows: Sequence[FindingInsert], *, transaction: LockedTransaction) -> None:
        self.writes.append(("findings", list(rows)))

    def insert_facts(self, rows: Sequence[FindingFactInsert], *, transaction: LockedTransaction) -> None:
        self.writes.append(("finding_facts", list(rows)))

    def record_marker(self, plan: ReviewFindingsPlan, *, transaction: LockedTransaction) -> None:
        self.writes.append(("marker", plan.chunk_id))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeReviewFindingsRepo) -> IWriteReviewFindingsRepository:
    return cast(IWriteReviewFindingsRepository, repo)


def _service(
    repo: _FakeReviewFindingsRepo,
    *,
    facts: IReadChunkFactsRepository | None = None,
    transactions: FakeTransactions | None = None,
) -> ReviewFindingsMaterialize:
    return ReviewFindingsMaterialize(
        delivery=_as_write_repo(repo),
        findings=cast(IWriteFindingRepository, repo),
        scopes=cast(IWriteScopeRepository, repo),
        facts=facts if facts is not None else FakeStatusFacts().as_port(),
        transactions=transactions if transactions is not None else FakeTransactions(),
        clock=FixedClock(instant=_T0),
    )


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
    service = _service(repo)
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
    service = _service(repo)
    validated = ValidatedReviewFindings(deferred=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    plan = repo.delivered[0]
    assert plan.new_findings == []
    assert plan.facts == []
    assert plan.scope_slugs == []


def test_deliver_over_two_entries_mints_two_distinct_findings() -> None:
    repo = _FakeReviewFindingsRepo()
    service = _service(repo)
    validated = ValidatedReviewFindings(deferred=[_deferred(ref="F1"), _deferred(ref="F2", scope="other")])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    plan = repo.delivered[0]
    assert len(plan.new_findings) == 2
    a, b = plan.new_findings
    assert a.finding_id != b.finding_id
    assert plan.scope_slugs == ["blizzard", "other"]


def test_deliver_returns_the_repository_outcome() -> None:
    repo = _FakeReviewFindingsRepo(outcome=ReviewFindingsOutcome.ALREADY_RECORDED)
    service = _service(repo)
    validated = ValidatedReviewFindings(deferred=[])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    assert outcome is ReviewFindingsOutcome.ALREADY_RECORDED


def test_deliver_writes_in_a_unit_of_its_own_that_locks_the_chunk() -> None:
    repo = _FakeReviewFindingsRepo()
    transactions = FakeTransactions()
    service = _service(repo, transactions=transactions)

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
    status_facts = FakeStatusFacts(facts)
    service = _service(repo, facts=status_facts.as_port())

    service.deliver(ValidatedReviewFindings(deferred=[]), chunk=_CHUNK, node=_NODE, epoch=1)

    assert repo.terminals == [terminal]
    assert status_facts.transactions == repo.written_in


def test_deliver_mints_each_named_scope_in_the_unit_then_writes_findings_then_facts_then_the_marker() -> None:
    repo = _FakeReviewFindingsRepo()
    validated = ValidatedReviewFindings(
        deferred=[_deferred(ref="F1"), _deferred(ref="F2", scope="other"), _deferred(ref="F3")]
    )

    outcome = _service(repo).deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    assert outcome is ReviewFindingsOutcome.RECORDED
    assert [kind for kind, _ in repo.writes] == ["scope", "scope", "findings", "finding_facts", "marker"]
    assert repo.writes[0] == ("scope", (_PROJECT, "blizzard", repo.written_in[0]))
    assert repo.writes[1] == ("scope", (_PROJECT, "other", repo.written_in[0]))


@pytest.mark.parametrize("outcome", [ReviewFindingsOutcome.ALREADY_RECORDED, ReviewFindingsOutcome.FENCED])
def test_deliver_a_refused_admission_writes_nothing(outcome: ReviewFindingsOutcome) -> None:
    repo = _FakeReviewFindingsRepo(outcome=outcome)

    assert (
        _service(repo).deliver(ValidatedReviewFindings(deferred=[_deferred()]), chunk=_CHUNK, node=_NODE, epoch=1)
        is outcome
    )
    assert repo.writes == []
