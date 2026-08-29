"""The garden-proposal candidate payload on the wire (unit tier, blizzard#390).

``GardenProposalCandidate.findings`` is required and non-empty (D7): malformed input is
refused at the wire edge, mechanically, before any delivery logic ever sees it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from blizzard.wire.garden_proposal import GardenProposalCandidate

pytestmark = pytest.mark.unit


def _candidate(**overrides: object) -> dict[str, object]:
    return {
        "ref": "P1",
        "class": "fix-the-source",
        "title": "Author a docstring standard",
        "body": "the case",
        "findings": ["fin_1", "fin_2"],
        **overrides,
    }


def test_a_well_formed_candidate_parses() -> None:
    candidate = GardenProposalCandidate.model_validate(_candidate())
    assert candidate.ref == "P1"
    assert candidate.class_ == "fix-the-source"
    assert candidate.findings == ["fin_1", "fin_2"]


def test_an_empty_findings_list_is_refused() -> None:
    with pytest.raises(ValidationError):
        GardenProposalCandidate.model_validate(_candidate(findings=[]))


def test_a_missing_findings_list_is_refused() -> None:
    with pytest.raises(ValidationError):
        GardenProposalCandidate.model_validate({k: v for k, v in _candidate().items() if k != "findings"})


def test_a_candidate_missing_its_class_is_refused() -> None:
    with pytest.raises(ValidationError):
        GardenProposalCandidate.model_validate({k: v for k, v in _candidate().items() if k != "class"})
