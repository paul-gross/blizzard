"""Review-finding delivery validation (unit tier, blizzard#582 Phase 1) — the
`record-findings` node's own shape check: a duplicate `ref`, a `deferred` entry missing
a required field, a `deferred` entry marked `blocking`, and a malformed scope slug each
raise `ReviewFindingsRejected`; a clean delta returns exactly its `deferred` entries
(the `tests/test_garden_delivery_domain.py` shape)."""

from __future__ import annotations

from typing import TypedDict

import pytest

from blizzard.hub.domain.review_findings import (
    ReviewFindingsRejected,
    parse_review_finding_delta,
    validate_review_findings,
)
from blizzard.wire.finding import ReviewFindingDelta, ReviewFindingEntry

pytestmark = pytest.mark.unit


class _DeferredFields(TypedDict, total=False):
    severity: str | None
    scope: str | None
    class_: str | None
    locus: str | None
    summary: str | None


def _deferred(
    ref: str = "F1",
    *,
    severity: str | None = "should-fix",
    scope: str | None = "blizzard",
    class_: str | None = "correctness",
    locus: str | None = "a.py:1",
    summary: str | None = "s",
) -> ReviewFindingEntry:
    return ReviewFindingEntry.model_validate(
        {
            "ref": ref,
            "disposition": "deferred",
            "severity": severity,
            "scope": scope,
            "class": class_,
            "locus": locus,
            "summary": summary,
        }
    )


def _fixed(ref: str = "F1") -> ReviewFindingEntry:
    return ReviewFindingEntry(ref=ref, disposition="fixed")


def _refuted(ref: str = "F1") -> ReviewFindingEntry:
    return ReviewFindingEntry(ref=ref, disposition="refuted")


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ReviewFindingsRejected, match="review-finding-delta"):
        parse_review_finding_delta("review-finding-delta", "not valid json")


def test_parse_accepts_a_well_formed_delta() -> None:
    delta = parse_review_finding_delta(
        "review-finding-delta",
        '{"entries": [{"ref": "F1", "disposition": "fixed"}]}',
    )
    assert delta.entries == [ReviewFindingEntry(ref="F1", disposition="fixed")]


def test_a_deferred_entry_survives_validation() -> None:
    delta = ReviewFindingDelta(entries=[_deferred()])

    validated = validate_review_findings(delta)

    assert len(validated.deferred) == 1
    assert validated.deferred[0].ref == "F1"


def test_fixed_and_refuted_entries_do_not_survive_validation() -> None:
    delta = ReviewFindingDelta(entries=[_fixed(ref="F1"), _refuted(ref="F2"), _deferred(ref="F3")])

    validated = validate_review_findings(delta)

    assert [e.ref for e in validated.deferred] == ["F3"]


def test_an_empty_delta_survives_validation() -> None:
    validated = validate_review_findings(ReviewFindingDelta(entries=[]))

    assert validated.deferred == []


def test_a_duplicate_ref_is_rejected() -> None:
    delta = ReviewFindingDelta(entries=[_fixed(ref="F1"), _deferred(ref="F1")])

    with pytest.raises(ReviewFindingsRejected, match="F1"):
        validate_review_findings(delta)


@pytest.mark.parametrize("missing", ["severity", "scope", "class_", "locus", "summary"])
def test_a_deferred_entry_missing_a_required_field_is_rejected(missing: str) -> None:
    kwargs: _DeferredFields = {missing: None}  # pyright: ignore[reportAssignmentType]
    delta = ReviewFindingDelta(entries=[_deferred(**kwargs)])

    with pytest.raises(ReviewFindingsRejected, match="F1"):
        validate_review_findings(delta)


def test_a_deferred_entry_marked_blocking_is_rejected() -> None:
    delta = ReviewFindingDelta(entries=[_deferred(severity="blocking")])

    with pytest.raises(ReviewFindingsRejected, match="blocking"):
        validate_review_findings(delta)


def test_a_deferred_entry_naming_a_malformed_scope_slug_is_rejected() -> None:
    delta = ReviewFindingDelta(entries=[_deferred(scope="Not A Slug")])

    with pytest.raises(ReviewFindingsRejected, match="scope"):
        validate_review_findings(delta)


def test_fixed_and_refuted_entries_carry_no_required_fields() -> None:
    """`fixed`/`refuted` entries pass with only `ref` and `disposition` set — the review
    already settled those, so validation asks nothing further of them."""
    delta = ReviewFindingDelta(entries=[_fixed(ref="F1"), _refuted(ref="F2")])

    validated = validate_review_findings(delta)

    assert validated.deferred == []
