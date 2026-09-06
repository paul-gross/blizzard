"""The finding delta payload on the wire (unit tier, blizzard#390).

``FindingDelta.findings`` is a discriminated union on ``op`` (the
``tests/test_work_item_proposals_wire.py`` shape): malformed input is refused at the
wire edge, mechanically, before any delivery logic ever sees it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from blizzard.wire.finding import FindingCandidate, FindingDelta, FindingDetailView, FindingFactView

pytestmark = pytest.mark.unit


def _delta(**overrides: object) -> dict[str, object]:
    return {"scope": "blizzard", **overrides}


def test_a_well_formed_add_op_parses() -> None:
    delta = FindingDelta.model_validate(
        _delta(findings=[{"op": "add", "class": "stale-docstring", "locus": "a.py:1", "summary": "s"}])
    )
    assert len(delta.findings) == 1
    op = delta.findings[0]
    assert op.op == "add"
    assert op.class_ == "stale-docstring"  # type: ignore[union-attr]


def test_a_well_formed_observed_op_parses() -> None:
    delta = FindingDelta.model_validate(_delta(findings=[{"op": "observed", "id": "fin_1"}]))
    op = delta.findings[0]
    assert op.op == "observed"
    assert op.id == "fin_1"  # type: ignore[union-attr]


def test_a_well_formed_gone_op_parses() -> None:
    delta = FindingDelta.model_validate(_delta(findings=[{"op": "gone", "id": "fin_1", "note": "gone"}]))
    op = delta.findings[0]
    assert op.op == "gone"
    assert op.note == "gone"  # type: ignore[union-attr]


def test_a_gone_op_missing_its_note_is_refused() -> None:
    with pytest.raises(ValidationError):
        FindingDelta.model_validate(_delta(findings=[{"op": "gone", "id": "fin_1"}]))


def test_an_add_op_missing_its_class_is_refused() -> None:
    with pytest.raises(ValidationError):
        FindingDelta.model_validate(_delta(findings=[{"op": "add", "locus": "a.py:1", "summary": "s"}]))


def test_an_unknown_op_is_refused() -> None:
    with pytest.raises(ValidationError):
        FindingDelta.model_validate(_delta(findings=[{"op": "bogus"}]))


def test_a_delta_with_no_findings_parses_exactly_as_before() -> None:
    delta = FindingDelta.model_validate(_delta())
    assert delta.findings == []
    assert delta.revisions == {}
    assert delta.measurement is None


def test_a_delta_carries_its_scope_revisions_and_measurement() -> None:
    delta = FindingDelta.model_validate(
        _delta(revisions={"blizzard": "a1b2c3d"}, measurement="23 files checked", findings=[])
    )
    assert delta.scope == "blizzard"
    assert delta.revisions == {"blizzard": "a1b2c3d"}
    assert delta.measurement == "23 files checked"


def test_a_well_formed_candidate_parses() -> None:
    candidate = FindingCandidate.model_validate(
        {"ref": "F1", "class": "stale-docstring", "locus": "a.py:1", "summary": "s", "introduced": "a1b2c3d"}
    )
    assert candidate.ref == "F1"
    assert candidate.class_ == "stale-docstring"
    assert candidate.introduced == "a1b2c3d"


def test_a_candidate_omitting_introduced_parses() -> None:
    candidate = FindingCandidate.model_validate(
        {"ref": "F1", "class": "stale-docstring", "locus": "a.py:1", "summary": "s"}
    )
    assert candidate.introduced is None


def _detail(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "finding_id": "fin_1",
        "routine_name": "nightly",
        "scope_slug": "blizzard",
        "class": "stale-docstring",
        "locus": "a.py:1",
        "summary": "s",
        "live": True,
        "state": "live",
        "last_seen_at": None,
        "observed_count": 0,
        "facts": [],
    }
    fields.update(overrides)
    return fields


def test_a_detail_view_with_an_empty_fact_chain_parses() -> None:
    view = FindingDetailView.model_validate(_detail())
    assert view.finding_id == "fin_1"
    assert view.class_ == "stale-docstring"
    assert view.facts == []


def test_a_detail_view_with_a_couple_of_facts_parses_oldest_first() -> None:
    view = FindingDetailView.model_validate(
        _detail(
            facts=[
                {"kind": "add", "recorded_at": "2026-07-16T12:00:00Z"},
                {
                    "kind": "resolved",
                    "recorded_at": "2026-07-17T09:00:00Z",
                    "note": "shipped it",
                    "actor": "pgross",
                    "proposal_id": "prop_1",
                },
            ]
        )
    )
    assert [f.kind for f in view.facts] == ["add", "resolved"]
    assert view.facts[0].note is None
    assert view.facts[0].actor is None
    assert view.facts[0].proposal_id is None
    assert view.facts[0].superseded_by is None
    assert view.facts[1].note == "shipped it"
    assert view.facts[1].actor == "pgross"
    assert view.facts[1].proposal_id == "prop_1"


def test_a_fact_view_with_only_kind_and_recorded_at_parses() -> None:
    fact = FindingFactView.model_validate({"kind": "add", "recorded_at": "2026-07-16T12:00:00Z"})
    assert fact.kind == "add"
    assert fact.note is None
    assert fact.actor is None
    assert fact.proposal_id is None
    assert fact.superseded_by is None
