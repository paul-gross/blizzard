"""``ReviewFindingsMaterialize`` (unit tier, blizzard#582 Phase 1): builds a
``ReviewFindingsPlan`` from a ``ValidatedReviewFindings`` over a fake repository — every
id mints with the right prefix, every field maps through from the wire entry, an empty
delta yields an empty plan (the ``tests/test_garden_delivery_materialize.py`` shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import FINDING_PREFIX
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import Node
from blizzard.hub.domain.review_findings import ValidatedReviewFindings
from blizzard.hub.domain.review_findings_materialize import (
    IWriteReviewFindingsRepository,
    ReviewFindingsMaterialize,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.domain.work import Chunk, WorkRef
from blizzard.wire.finding import ReviewFindingEntry

pytestmark = pytest.mark.unit

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
    delivered: list[ReviewFindingsPlan] = field(default_factory=list)
    outcome: ReviewFindingsOutcome = ReviewFindingsOutcome.RECORDED

    def deliver(self, plan: ReviewFindingsPlan) -> ReviewFindingsOutcome:
        self.delivered.append(plan)
        return self.outcome

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_repo(repo: _FakeReviewFindingsRepo) -> IWriteReviewFindingsRepository:
    return cast(IWriteReviewFindingsRepository, repo)


def _deferred(ref: str = "F1", *, scope: str = "blizzard", severity: str = "should-fix") -> ReviewFindingEntry:
    return ReviewFindingEntry.model_validate(
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
    service = ReviewFindingsMaterialize(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
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

    assert len(plan.new_findings) == 1
    finding = plan.new_findings[0]
    assert finding.finding_id.startswith(f"{FINDING_PREFIX}_")
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
    service = ReviewFindingsMaterialize(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    validated = ValidatedReviewFindings(deferred=[])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    plan = repo.delivered[0]
    assert plan.new_findings == []
    assert plan.facts == []
    assert plan.scope_slugs == []


def test_deliver_over_two_entries_mints_two_distinct_findings() -> None:
    repo = _FakeReviewFindingsRepo()
    service = ReviewFindingsMaterialize(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    validated = ValidatedReviewFindings(deferred=[_deferred(ref="F1"), _deferred(ref="F2", scope="other")])

    service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    plan = repo.delivered[0]
    assert len(plan.new_findings) == 2
    a, b = plan.new_findings
    assert a.finding_id != b.finding_id
    assert plan.scope_slugs == ["blizzard", "other"]


def test_deliver_returns_the_repository_outcome() -> None:
    repo = _FakeReviewFindingsRepo(outcome=ReviewFindingsOutcome.ALREADY_RECORDED)
    service = ReviewFindingsMaterialize(delivery=_as_write_repo(repo), clock=FixedClock(instant=_T0))
    validated = ValidatedReviewFindings(deferred=[])

    outcome = service.deliver(validated, chunk=_CHUNK, node=_NODE, epoch=1)

    assert outcome is ReviewFindingsOutcome.ALREADY_RECORDED
