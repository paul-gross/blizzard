"""The proposed-work-item payload on the wire (unit tier).

``CompletionSubmission.proposals`` is a discriminated union on ``kind`` (D1): malformed
input is refused at the wire edge, mechanically, before ``ApplyService`` ever sees it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from blizzard.wire.completion import CompletionSubmission

pytestmark = pytest.mark.unit


def _submission(**overrides: object) -> dict[str, object]:
    return {
        "choice": "pass",
        "epoch": 1,
        "runner_id": "run_1",
        "from_node_id": "nd_1",
        **overrides,
    }


def test_a_well_formed_create_proposal_parses() -> None:
    submission = CompletionSubmission.model_validate(
        _submission(proposals=[{"kind": "create", "title": "t", "body": "b", "stated_priority": "high"}])
    )
    assert len(submission.proposals) == 1
    proposal = submission.proposals[0]
    assert proposal.kind == "create"
    assert proposal.title == "t"  # type: ignore[union-attr]


def test_a_well_formed_update_proposal_parses() -> None:
    submission = CompletionSubmission.model_validate(
        _submission(proposals=[{"kind": "update", "source": "default", "ref": "9", "evidence": "e"}])
    )
    assert len(submission.proposals) == 1
    proposal = submission.proposals[0]
    assert proposal.kind == "update"
    assert proposal.ref == "9"  # type: ignore[union-attr]


def test_a_create_proposal_missing_its_title_is_refused() -> None:
    with pytest.raises(ValidationError):
        CompletionSubmission.model_validate(_submission(proposals=[{"kind": "create", "body": "b"}]))


def test_an_update_proposal_missing_its_pointer_is_refused() -> None:
    with pytest.raises(ValidationError):
        CompletionSubmission.model_validate(_submission(proposals=[{"kind": "update", "evidence": "e"}]))


def test_an_unknown_proposal_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        CompletionSubmission.model_validate(_submission(proposals=[{"kind": "bogus"}]))


def test_a_completion_with_no_proposals_parses_exactly_as_before() -> None:
    submission = CompletionSubmission.model_validate(_submission())
    assert submission.proposals == []
