"""The work item's own rules, pinned by value: its state, the declared verb table, the
non-blank text invariant, and an edit's resolution against the item it targets."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor
from blizzard.hub.domain.work_items.model import (
    Transition,
    WorkItemEdit,
    WorkItemFieldBlank,
    WorkItemNotEditable,
    WorkItemRevision,
    WorkItemState,
    WorkItemText,
    WorkItemVerb,
    is_readable,
    require_open_for,
    require_text,
    require_withdrawn,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _item(closure: WorkItemClosure | None = None, *, stated_priority: str | None = "high") -> HubWorkItem:
    return HubWorkItem(
        work_item_id="wi_1",
        source="hub",
        ref="7",
        title="old title",
        body="old body",
        author=WorkItemAuthor.user("u_1"),
        stated_priority=stated_priority,
        created_at=_AT,
        edited_at=_AT,
        closed_at=_AT if closure is not None else None,
        closure=closure,
    )


@pytest.mark.parametrize(
    ("closure", "state"),
    [
        (None, WorkItemState.OPEN),
        (WorkItemClosure.DELIVERED, WorkItemState.DELIVERED),
        (WorkItemClosure.WITHDRAWN, WorkItemState.WITHDRAWN),
    ],
)
def test_an_items_state_is_open_until_it_carries_a_closure_then_that_closure(
    closure: WorkItemClosure | None, state: WorkItemState
) -> None:
    assert WorkItemState.of(_item(closure)) is state


_A, _N, _R = Transition.APPLY, Transition.NOOP, Transition.REFUSE


@pytest.mark.parametrize(
    ("verb", "from_open", "from_delivered", "from_withdrawn"),
    [
        (WorkItemVerb.EDIT, _A, _R, _R),
        (WorkItemVerb.WITHDRAW, _A, _R, _R),
        (WorkItemVerb.APPEND_EVIDENCE, _A, _R, _R),
        (WorkItemVerb.DELIVER, _A, _N, _N),
        (WorkItemVerb.CASCADE_WITHDRAW, _A, _N, _N),
        (WorkItemVerb.READ, _A, _A, _R),
    ],
)
def test_the_verb_table_declares_every_verb_from_every_state(
    verb: WorkItemVerb, from_open: Transition, from_delivered: Transition, from_withdrawn: Transition
) -> None:
    assert WorkItemState.OPEN.on(verb) is from_open
    assert WorkItemState.DELIVERED.on(verb) is from_delivered
    assert WorkItemState.WITHDRAWN.on(verb) is from_withdrawn


@pytest.mark.parametrize("verb", [WorkItemVerb.EDIT, WorkItemVerb.WITHDRAW])
def test_an_operator_verb_on_an_open_item_passes(verb: WorkItemVerb) -> None:
    require_open_for(_item(), verb)


@pytest.mark.parametrize("closure", [WorkItemClosure.DELIVERED, WorkItemClosure.WITHDRAWN])
@pytest.mark.parametrize("verb", [WorkItemVerb.EDIT, WorkItemVerb.WITHDRAW])
def test_an_operator_verb_on_a_closed_item_is_refused_naming_its_closure(
    verb: WorkItemVerb, closure: WorkItemClosure
) -> None:
    with pytest.raises(WorkItemNotEditable) as caught:
        require_open_for(_item(closure), verb)
    assert caught.value.work_item_id == "wi_1"
    assert caught.value.closure is closure
    assert str(caught.value) == f"work item wi_1 is {closure.value}, not editable"


def test_deliver_never_refuses_so_require_open_for_admits_it_from_any_state() -> None:
    for closure in (None, WorkItemClosure.DELIVERED, WorkItemClosure.WITHDRAWN):
        require_open_for(_item(closure), WorkItemVerb.DELIVER)


def test_a_delivered_item_stays_readable_and_a_withdrawn_one_does_not() -> None:
    assert is_readable(_item()) is True
    assert is_readable(_item(WorkItemClosure.DELIVERED)) is True
    assert is_readable(_item(WorkItemClosure.WITHDRAWN)) is False


def test_require_text_strips() -> None:
    assert require_text("  hello \n", "title") == "hello"


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_require_text_refuses_blank_naming_the_field(value: str) -> None:
    with pytest.raises(WorkItemFieldBlank) as caught:
        require_text(value, "body")
    assert caught.value.field_name == "body"
    assert str(caught.value) == "body must not be blank"


def test_work_item_text_strips_both_fields() -> None:
    assert WorkItemText.of(title=" t ", body=" b ") == WorkItemText(title="t", body="b")


def test_work_item_text_checks_the_title_before_the_body() -> None:
    with pytest.raises(WorkItemFieldBlank) as caught:
        WorkItemText.of(title=" ", body="")
    assert caught.value.field_name == "title"


def test_an_edit_strips_a_supplied_title_and_body() -> None:
    edit = WorkItemEdit(title=" new ", body=" text ")
    assert edit.title == "new"
    assert edit.body == "text"


@pytest.mark.parametrize(("field_name", "kwargs"), [("title", {"title": " "}), ("body", {"body": ""})])
def test_an_edit_refuses_a_blank_supplied_field(field_name: str, kwargs: dict[str, str]) -> None:
    with pytest.raises(WorkItemFieldBlank) as caught:
        WorkItemEdit(**kwargs)  # pyright: ignore[reportArgumentType]
    assert caught.value.field_name == field_name


def test_an_empty_edit_resolves_to_the_items_own_fields() -> None:
    assert WorkItemEdit().resolve_against(_item()) == WorkItemRevision(
        title="old title", body="old body", stated_priority="high"
    )


def test_an_edit_replaces_only_the_fields_it_names() -> None:
    assert WorkItemEdit(body="new body").resolve_against(_item()) == WorkItemRevision(
        title="old title", body="new body", stated_priority="high"
    )


def test_an_explicit_none_priority_clears_it_and_a_priority_is_stored_by_value() -> None:
    assert WorkItemEdit(stated_priority=None).resolve_against(_item()).stated_priority is None
    assert (
        WorkItemEdit(stated_priority=WorkItemPriority.LOW).resolve_against(_item(stated_priority=None)).stated_priority
        == WorkItemPriority.LOW.value
    )


def test_a_withdraw_that_landed_stands_as_withdrawn() -> None:
    require_withdrawn(_item(WorkItemClosure.WITHDRAWN))


def test_a_withdraw_a_delivery_raced_is_refused_naming_the_delivery() -> None:
    with pytest.raises(WorkItemNotEditable) as caught:
        require_withdrawn(_item(WorkItemClosure.DELIVERED))
    assert caught.value.closure is WorkItemClosure.DELIVERED
