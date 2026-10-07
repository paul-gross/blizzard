"""Review-findings delivery rules (unit tier), pinned by value with no repository and no
clock: a chunk must carry its review delta, the description of a scope a review mints,
the plan a passing review mints, and the once-per-chunk replay verb."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import Chunk, WorkRef
from blizzard.hub.domain.garden.formats import DeferredReviewEntry
from blizzard.hub.domain.garden.review.materialize import (
    NewReviewFinding,
    NewReviewFindingFact,
    ReviewFindingsOutcome,
    build_review_plan,
    review_replay_outcome,
    review_scope_description,
)
from blizzard.hub.domain.garden.review.validation import (
    ReviewFindingsRejected,
    ValidatedReviewFindings,
    require_review_delta,
)
from blizzard.hub.domain.graph.model import Node
from tests.garden_artifacts import deferred_entry

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="ch_9", graph_id="gr_1", work_refs=[WorkRef(source="default", ref="1")], minted_at=_AT)
_NODE = Node(
    node_id="nd_rf",
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


def _deferred(ref: str, *, scope: str = "blizzard") -> DeferredReviewEntry:
    return deferred_entry(
        {
            "ref": ref,
            "disposition": "deferred",
            "severity": "should-fix",
            "scope": scope,
            "class": "naming",
            "locus": "a.py:1",
            "summary": f"summary {ref}",
        }
    )


def test_a_chunk_with_no_review_delta_refuses() -> None:
    with pytest.raises(ReviewFindingsRejected) as exc:
        require_review_delta(None, chunk_id="ch_9")
    assert str(exc.value) == "no 'review-finding-delta' artifact found for chunk ch_9"


def test_a_present_review_delta_is_returned() -> None:
    artifact = StoredArtifact(
        kind=ArtifactKind.ASSET,
        name="review-finding-delta",
        data="{}",
        repo=None,
        forge=None,
        artifact_id="art_1",
        chunk_id="ch_9",
        node_id="nd_review",
        node_name="review",
        epoch=1,
    )

    assert require_review_delta(artifact, chunk_id="ch_9") is artifact


def test_a_minted_scope_is_described_by_its_chunk() -> None:
    assert review_scope_description(_CHUNK) == "Minted by review delivery on chunk ch_9"


def test_each_deferred_entry_mints_one_finding_and_one_add_fact_in_order() -> None:
    plan = build_review_plan(
        ValidatedReviewFindings(deferred=[_deferred("F1"), _deferred("F2", scope="runner")]),
        chunk=_CHUNK,
        node=_NODE,
        epoch=2,
        at=_AT,
    )

    assert (plan.chunk_id, plan.node_id, plan.node_name, plan.epoch, plan.at) == (
        "ch_9",
        "nd_rf",
        "record-findings",
        2,
        _AT,
    )
    assert plan.new_scope_description == "Minted by review delivery on chunk ch_9"
    ids = [finding.finding_id for finding in plan.new_findings]
    assert all(Id.parse(i) is not None and i.startswith(f"{IdPrefix.FINDING}_") for i in ids)
    assert len(set(ids)) == 2
    assert plan.new_findings == [
        NewReviewFinding(
            finding_id=ids[0],
            scope_slug="blizzard",
            class_="naming",
            locus="a.py:1",
            summary="summary F1",
            severity="should-fix",
            raised_by_chunk_id="ch_9",
        ),
        NewReviewFinding(
            finding_id=ids[1],
            scope_slug="runner",
            class_="naming",
            locus="a.py:1",
            summary="summary F2",
            severity="should-fix",
            raised_by_chunk_id="ch_9",
        ),
    ]
    assert plan.facts == [
        NewReviewFindingFact(finding_id=ids[0], ref="F1"),
        NewReviewFindingFact(finding_id=ids[1], ref="F2"),
    ]


def test_a_review_with_nothing_deferred_mints_nothing() -> None:
    plan = build_review_plan(ValidatedReviewFindings(), chunk=_CHUNK, node=_NODE, epoch=1, at=_AT)

    assert (plan.new_findings, plan.facts) == ([], [])


def test_a_recorded_chunk_replays_as_already_recorded() -> None:
    assert review_replay_outcome(recorded=True) is ReviewFindingsOutcome.ALREADY_RECORDED


def test_an_unrecorded_chunk_proceeds_to_validation() -> None:
    assert review_replay_outcome(recorded=False) is None
