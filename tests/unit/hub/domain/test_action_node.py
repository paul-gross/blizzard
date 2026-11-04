"""A hub node naming an action: its mint rules, its due-ness, and the flow driver re-driving it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack

import pytest

from blizzard.hub.domain.chunk.model import HubNodePollFact
from blizzard.hub.domain.delivery.flows import PollPolicy
from blizzard.hub.domain.graph.actions import SHIPPED_CATALOGUE, TEST_ANSWER, HubActionCatalogue
from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from tests.support import TEST_CATALOGUE

_BUILD = {
    "executor": "runner",
    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "deliver"}}},
}

_LAND_CHOICES = {
    name: {"description": name, "to": "done" if name == "landed" else "build"}
    for name in ("landed", "conflict", "failure", "inherited-failure")
}


class _DeliverKeys(TypedDict, total=False):
    action: str
    judgement: dict[str, object]
    poll_interval: int
    poll_timeout: int


def _deliver(**overrides: Unpack[_DeliverKeys]) -> dict[str, object]:
    node: dict[str, object] = {"executor": "hub", "action": "land", "judgement": {"choices": dict(_LAND_CHOICES)}}
    node.update(overrides)
    return node


def _errors(deliver: dict[str, object], *, catalogue: HubActionCatalogue = TEST_CATALOGUE) -> list[str]:
    doc = GraphDoc.of({"name": "g", "entry": "build", "nodes": {"build": _BUILD, "deliver": deliver}})
    return Validator.of(doc, catalogue).result.errors


def _choices(*names: str) -> dict[str, object]:
    return {"choices": {name: {"description": name, "to": "done"} for name in names}}


# Unit — the node's mint rules


def test_a_hub_node_naming_a_routed_action_mints() -> None:
    assert _errors(_deliver(poll_interval=30, poll_timeout=10800)) == []


def test_a_hub_node_naming_no_action_is_refused() -> None:
    errors = _errors({"executor": "hub", "judgement": _choices("success", "failure")})
    assert any("must name an `action:`" in e for e in errors)


def test_an_action_on_a_runner_node_is_refused() -> None:
    doc = GraphDoc.of({"name": "g", "entry": "build", "nodes": {"build": {**_BUILD, "action": "pass"}}})
    assert any("`action:` is only legal on a hub node" in e for e in Validator.of(doc, TEST_CATALOGUE).result.errors)


def test_an_unknown_action_is_refused() -> None:
    assert any("unknown action `nope`" in e for e in _errors(_deliver(action="nope")))


def test_a_test_action_is_unknown_to_the_shipped_catalogue() -> None:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "test.answer",
        "with": {"choice": "failure"},
        "judgement": _choices("failure"),
    }
    assert _errors(node) == []
    assert any("unknown action `test.answer`" in e for e in _errors(node, catalogue=SHIPPED_CATALOGUE))


def test_an_undeclared_with_key_is_refused() -> None:
    node: dict[str, object] = {"executor": "hub", "action": "garden-deliver", "with": {"bogus": "x"}}
    node["judgement"] = _choices("recorded", "invalid", "failure")
    assert any("declares no argument `bogus`" in e for e in _errors(node))


def test_a_missing_required_with_key_is_refused() -> None:
    node: dict[str, object] = {"executor": "hub", "action": "test.answer", "judgement": _choices("failure")}
    assert any("requires argument `with.choice`" in e for e in _errors(node))


def test_a_with_value_that_is_not_a_string_or_list_of_strings_is_refused() -> None:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "garden-deliver",
        "with": {"delta": 3, "proposals": ["docket", 4]},
    }
    node["judgement"] = _choices("recorded", "invalid", "failure")
    errors = _errors(node)
    assert any("`with.delta` must be a string or a list of strings" in e for e in errors)
    assert any("`with.proposals` must be a string or a list of strings" in e for e in errors)


def test_with_on_a_hub_node_naming_no_action_is_refused() -> None:
    node: dict[str, object] = {"executor": "hub", "with": {"x": "y"}, "judgement": _choices("failure")}
    assert any("`with:` is only legal on a node that names an `action:`" in e for e in _errors(node))


def test_an_answer_the_node_authors_no_choice_for_is_refused() -> None:
    choices = {k: v for k, v in _LAND_CHOICES.items() if k != "inherited-failure"}
    errors = _errors(_deliver(judgement={"choices": choices}))
    assert any("can answer `inherited-failure`" in e for e in errors)


def test_a_garden_deliver_node_without_invalid_proposals_is_refused() -> None:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "garden-deliver",
        "with": {"delta": "delta", "proposals": "docket"},
        "judgement": _choices("recorded", "invalid", "failure"),
    }
    assert any("can answer `invalid-proposals`" in e for e in _errors(node))


def test_a_test_actions_answers_are_its_arguments_values() -> None:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "test.answer",
        "with": {"choice": "landed"},
        "judgement": _choices("failure"),
    }
    assert any("can answer `landed`" in e for e in _errors(node))
    assert TEST_ANSWER.answers_for({"choice": ("landed",)}) == frozenset({"landed"})


def test_a_choice_named_by_a_reserved_wait_is_refused() -> None:
    errors = _errors(_deliver(judgement={"choices": {**_LAND_CHOICES, "pending": {"description": "p", "to": "done"}}}))
    assert any("`pending` is a reserved wait" in e for e in errors)


@pytest.mark.parametrize("action", ["record-findings", "garden-deliver", "pass"])
def test_poll_keys_on_an_action_that_cannot_wait_are_refused(action: str) -> None:
    answers = SHIPPED_CATALOGUE.specs[action].answers
    node: dict[str, object] = {
        "executor": "hub",
        "action": action,
        "poll_interval": 30,
        "judgement": _choices(*answers, "failure"),
    }
    assert any("`poll_interval`/`poll_timeout` are only legal" in e for e in _errors(node))


def test_poll_keys_on_a_test_action_that_can_answer_a_wait_mint() -> None:
    node: dict[str, object] = {
        "executor": "hub",
        "action": "test.sequence",
        "with": {"choices": ["pending", "failure"]},
        "poll_interval": 30,
        "judgement": _choices("failure"),
    }
    assert _errors(node) == []


# Unit — due-ness


_AT = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def test_a_waiting_visit_is_due_at_its_interval_without_a_hint() -> None:
    row = HubNodePollFact(node_id="nd", epoch=1, polled_at=_AT)
    assert PollPolicy(interval=timedelta(seconds=30)).due_at(row) == _AT + timedelta(seconds=30)


def test_a_later_next_poll_at_postpones_the_visit() -> None:
    row = HubNodePollFact(node_id="nd", epoch=1, polled_at=_AT, next_poll_at=_AT + timedelta(minutes=5))
    assert PollPolicy(interval=timedelta(seconds=30)).due_at(row) == _AT + timedelta(minutes=5)


def test_an_earlier_next_poll_at_never_hastens_the_visit() -> None:
    row = HubNodePollFact(node_id="nd", epoch=1, polled_at=_AT, next_poll_at=_AT + timedelta(seconds=5))
    assert PollPolicy(interval=timedelta(seconds=30)).due_at(row) == _AT + timedelta(seconds=30)


def test_parked_wait_uses_latest_poll_and_two_minute_floor() -> None:
    row = HubNodePollFact(
        node_id="nd",
        epoch=1,
        polled_at=_AT,
        kind="awaiting-merge",
        last_polled_at=_AT + timedelta(minutes=3),
    )
    assert PollPolicy(interval=timedelta(seconds=30)).due_at(row) == _AT + timedelta(minutes=5)
    assert PollPolicy(interval=timedelta(minutes=4)).due_at(row) == _AT + timedelta(minutes=7)
