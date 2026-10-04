"""How a fleet work-item proposal is judged, pinned by value: parsing a stamped row into
the work to do or an unresolvable judgment, and judging an update's target item."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.domain.work_items.model import WorkItemText
from blizzard.hub.domain.work_items.proposal_rules import (
    CreateProposal,
    Unresolvable,
    UpdateProposal,
    judge_update_target,
    parse_proposal,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_POINTER = WorkRef(source="hub", ref="7")


def _row(kind: str, data: object, *, runner_id: str | None = "r_1") -> StampedWorkItemProposal:
    return StampedWorkItemProposal(
        proposal_id="p_1",
        chunk_id="ch_1",
        node_id="n_1",
        node_name="build",
        epoch=1,
        ordinal=0,
        kind=kind,
        data=data if isinstance(data, str) else json.dumps(data),
        runner_id=runner_id,
    )


def _item(closure: WorkItemClosure | None = None) -> HubWorkItem:
    return HubWorkItem(
        work_item_id="wi_1",
        source="hub",
        ref="7",
        title="t",
        body="b",
        author=WorkItemAuthor.user("u_1"),
        stated_priority=None,
        created_at=_AT,
        edited_at=_AT,
        closed_at=_AT if closure is not None else None,
        closure=closure,
    )


def test_a_create_parses_into_stripped_text_and_its_fleet_author() -> None:
    row = _row("create", {"title": " t ", "body": "b\n", "stated_priority": "high"})
    assert parse_proposal(row) == CreateProposal(
        text=WorkItemText(title="t", body="b"),
        stated_priority="high",
        author=WorkItemAuthor.fleet(runner_id="r_1", chunk_id="ch_1", node_name="build"),
    )


def test_a_create_with_no_stated_priority_carries_none() -> None:
    proposal = parse_proposal(_row("create", {"title": "t", "body": "b"}))
    assert isinstance(proposal, CreateProposal)
    assert proposal.stated_priority is None


def test_a_create_with_no_proposing_runner_is_unresolvable() -> None:
    row = _row("create", {"title": "t", "body": "b"}, runner_id=None)
    assert parse_proposal(row) == Unresolvable(reason="no proposing runner recorded for this proposal")


def test_no_proposing_runner_is_judged_before_the_fields_are_read() -> None:
    row = _row("create", {}, runner_id=None)
    assert parse_proposal(row) == Unresolvable(reason="no proposing runner recorded for this proposal")


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ({"title": " ", "body": "b"}, "title must not be blank"),
        ({"title": "t", "body": ""}, "body must not be blank"),
        ({"title": "", "body": ""}, "title must not be blank"),
    ],
)
def test_a_create_with_a_blank_title_or_body_is_unresolvable(data: dict[str, str], reason: str) -> None:
    assert parse_proposal(_row("create", data)) == Unresolvable(reason=reason)


def test_an_update_parses_into_its_pointer_and_evidence() -> None:
    row = _row("update", {"source": "hub", "ref": "7", "evidence": "seen again"})
    assert parse_proposal(row) == UpdateProposal(pointer=_POINTER, evidence="seen again")


@pytest.mark.parametrize(
    ("kind", "data", "reason"),
    [
        ("create", "{not json", "malformed proposal data: Expecting property name enclosed in double quotes"),
        ("create", {"body": "b"}, "malformed proposal data: 'title'"),
        ("update", {"source": "hub", "ref": "7"}, "malformed proposal data: 'evidence'"),
        ("update", ["hub", "7"], "malformed proposal data: list indices must be integers or slices, not str"),
    ],
)
def test_data_that_is_not_the_object_its_kind_needs_is_unresolvable_as_malformed(
    kind: str, data: object, reason: str
) -> None:
    judgment = parse_proposal(_row(kind, data))
    assert isinstance(judgment, Unresolvable)
    assert judgment.malformed is True
    assert judgment.pointer is None
    assert judgment.reason.startswith(reason)


def test_an_update_through_a_source_with_no_editor_is_unresolvable() -> None:
    pointer = WorkRef(source="forge", ref="9")
    assert judge_update_target(None, pointer=pointer, editable=False) == Unresolvable(
        reason="source 'forge' has no editor", pointer=pointer
    )


def test_an_update_of_a_missing_item_is_unresolvable() -> None:
    assert judge_update_target(None, pointer=_POINTER, editable=True) == Unresolvable(
        reason="item does not exist", pointer=_POINTER
    )


@pytest.mark.parametrize("closure", [WorkItemClosure.DELIVERED, WorkItemClosure.WITHDRAWN])
def test_an_update_of_a_closed_item_is_unresolvable_naming_its_closure(closure: WorkItemClosure) -> None:
    assert judge_update_target(_item(closure), pointer=_POINTER, editable=True) == Unresolvable(
        reason=f"item is {closure.value}", pointer=_POINTER
    )


def test_an_update_of_an_open_item_on_an_editable_source_appends() -> None:
    assert judge_update_target(_item(), pointer=_POINTER, editable=True) is None
