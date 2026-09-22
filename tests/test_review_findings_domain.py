"""Review-finding delivery validation (unit tier, blizzard#582 Phase 1) — the
`record-findings` node's own shape check: a duplicate `ref`, a `deferred` entry marked
`blocking`, and a malformed scope slug each raise `ReviewFindingsRejected`; a `deferred`
entry missing a required field never reaches this validator, since `ReviewFindingDelta`
itself refuses to parse one; a clean delta returns exactly its `deferred` entries (the
`tests/test_garden_delivery_domain.py` shape)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from blizzard.hub.domain.review_findings import (
    ReviewFindingsRejected,
    parse_review_finding_delta,
    validate_review_findings,
)
from blizzard.wire.finding import (
    DeferredReviewFindingEntry,
    FixedReviewFindingEntry,
    RefutedReviewFindingEntry,
    ReviewFindingDelta,
)

pytestmark = pytest.mark.unit


def _deferred(
    ref: str = "F1",
    *,
    severity: str = "should-fix",
    scope: str = "blizzard",
    class_: str = "correctness",
    locus: str = "a.py:1",
    summary: str = "s",
) -> DeferredReviewFindingEntry:
    return DeferredReviewFindingEntry.model_validate(
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


def _fixed(ref: str = "F1") -> FixedReviewFindingEntry:
    return FixedReviewFindingEntry(ref=ref)


def _refuted(ref: str = "F1") -> RefutedReviewFindingEntry:
    return RefutedReviewFindingEntry(ref=ref)


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ReviewFindingsRejected, match="review-finding-delta"):
        parse_review_finding_delta("review-finding-delta", "not valid json")


def test_parse_rejects_a_payload_with_no_entries_key() -> None:
    """review:F1 — `{}` has no `entries` key at all and must be refused, not read as an
    empty, `recorded` delta."""
    with pytest.raises(ReviewFindingsRejected, match="review-finding-delta"):
        parse_review_finding_delta("review-finding-delta", "{}")


def test_parse_rejects_a_payload_shaped_around_the_wrong_top_level_key() -> None:
    """review:F1 — a differently-named top-level key (here `findings`, the sibling
    garden format's own key) must not be silently ignored down to an empty delta."""
    with pytest.raises(ReviewFindingsRejected, match="review-finding-delta"):
        parse_review_finding_delta("review-finding-delta", '{"findings": [{"bogus": 1}]}')


def test_parse_accepts_a_well_formed_delta() -> None:
    delta = parse_review_finding_delta(
        "review-finding-delta",
        '{"entries": [{"ref": "F1", "disposition": "fixed"}]}',
    )
    assert delta.entries == [FixedReviewFindingEntry(ref="F1")]


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


@pytest.mark.parametrize("missing", ["severity", "scope", "class", "locus", "summary"])
def test_a_deferred_entry_missing_a_required_field_is_rejected(missing: str) -> None:
    """review:F1/review:F8 — the wire model itself refuses a `deferred` entry missing a
    required field; there is no downstream domain check left to exercise."""
    payload: dict[str, Any] = {
        "ref": "F1",
        "disposition": "deferred",
        "severity": "should-fix",
        "scope": "blizzard",
        "class": "correctness",
        "locus": "a.py:1",
        "summary": "s",
    }
    del payload[missing]

    with pytest.raises(ValidationError):
        DeferredReviewFindingEntry.model_validate(payload)


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


def test_a_deferred_entry_with_an_unknown_extra_field_is_rejected() -> None:
    """review:F1 — an unknown key on an otherwise well-formed entry is refused, not
    silently dropped."""
    payload = {
        "ref": "F1",
        "disposition": "deferred",
        "severity": "should-fix",
        "scope": "blizzard",
        "class": "correctness",
        "locus": "a.py:1",
        "summary": "s",
        "bogus": 1,
    }
    with pytest.raises(ValidationError):
        DeferredReviewFindingEntry.model_validate(payload)
