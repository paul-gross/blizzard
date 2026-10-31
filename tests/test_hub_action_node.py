"""A hub node naming an action: its mint rules, its due-ness, and the flow driver re-driving it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.chunk.model import HubNodePollFact
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.delivery.flows import PollPolicy
from blizzard.hub.domain.graph.actions import SHIPPED_CATALOGUE, TEST_ANSWER
from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from blizzard.hub.store import schema as s
from tests.support import TEST_CATALOGUE, build_hub, default_tenant, pointer_token, report_lease

pytestmark = pytest.mark.unit

_BUILD = {
    "executor": "runner",
    "judgement": {"prompt": "p", "choices": {"pass": {"description": "d", "to": "deliver"}}},
}

_LAND_CHOICES = {
    name: {"description": name, "to": "done" if name == "landed" else "build"}
    for name in ("landed", "conflict", "failure", "inherited-failure")
}


def _deliver(**overrides: object) -> dict[str, object]:
    node: dict[str, object] = {"executor": "hub", "action": "land", "judgement": {"choices": dict(_LAND_CHOICES)}}
    node.update(overrides)
    return node


def _errors(deliver: dict[str, object], *, catalogue=TEST_CATALOGUE) -> list[str]:  # type: ignore[no-untyped-def]
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
    node = {"executor": "hub", "action": "test.answer", "with": {"choice": "failure"}, "judgement": _choices("failure")}
    assert _errors(node) == []
    assert any("unknown action `test.answer`" in e for e in _errors(node, catalogue=SHIPPED_CATALOGUE))


def test_an_undeclared_with_key_is_refused() -> None:
    node = {"executor": "hub", "action": "garden-deliver", "with": {"bogus": "x"}}
    node["judgement"] = _choices("recorded", "invalid", "failure")
    assert any("declares no argument `bogus`" in e for e in _errors(node))


def test_a_missing_required_with_key_is_refused() -> None:
    node = {"executor": "hub", "action": "test.answer", "judgement": _choices("failure")}
    assert any("requires argument `with.choice`" in e for e in _errors(node))


def test_a_with_value_that_is_not_a_string_or_list_of_strings_is_refused() -> None:
    node = {"executor": "hub", "action": "garden-deliver", "with": {"delta": 3, "proposals": ["docket", 4]}}
    node["judgement"] = _choices("recorded", "invalid", "failure")
    errors = _errors(node)
    assert any("`with.delta` must be a string or a list of strings" in e for e in errors)
    assert any("`with.proposals` must be a string or a list of strings" in e for e in errors)


def test_with_on_a_hub_node_naming_no_action_is_refused() -> None:
    node = {"executor": "hub", "with": {"x": "y"}, "judgement": _choices("failure")}
    assert any("`with:` is only legal on a node that names an `action:`" in e for e in _errors(node))


def test_an_answer_the_node_authors_no_choice_for_is_refused() -> None:
    choices = {k: v for k, v in _LAND_CHOICES.items() if k != "inherited-failure"}
    errors = _errors(_deliver(judgement={"choices": choices}))
    assert any("can answer `inherited-failure`" in e for e in errors)


def test_a_test_actions_answers_are_its_arguments_values() -> None:
    node = {"executor": "hub", "action": "test.answer", "with": {"choice": "landed"}, "judgement": _choices("failure")}
    assert any("can answer `landed`" in e for e in _errors(node))
    assert TEST_ANSWER.answers_for({"choice": ("landed",)}) == frozenset({"landed"})


def test_a_choice_named_by_a_reserved_wait_is_refused() -> None:
    errors = _errors(_deliver(judgement={"choices": {**_LAND_CHOICES, "pending": {"description": "p", "to": "done"}}}))
    assert any("`pending` is a reserved wait" in e for e in errors)


@pytest.mark.parametrize("action", ["record-findings", "garden-deliver", "pass"])
def test_poll_keys_on_an_action_that_cannot_wait_are_refused(action: str) -> None:
    answers = SHIPPED_CATALOGUE.specs[action].answers
    node = {"executor": "hub", "action": action, "poll_interval": 30, "judgement": _choices(*answers, "failure")}
    assert any("`poll_interval`/`poll_timeout` are only legal" in e for e in _errors(node))


def test_poll_keys_on_a_test_action_that_can_answer_a_wait_mint() -> None:
    node = {
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


# Component — hub-advance re-drives a chunk waiting at a node naming an action


_ACTION_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build the change.
    judgement:
      prompt: Assess the build.
      choices:
        pass: {description: Green., to: deliver}
  deliver:
    executor: hub
    action: land
    poll_interval: 30
    poll_timeout: 10800
    judgement:
      choices:
        landed: {description: Landed., to: done}
        conflict: {description: Conflicted., to: build}
        failure: {description: Failed., to: build}
        inherited-failure: {description: Base is red., to: build}
"""


@pytest.mark.component
def test_the_flow_driver_re_drives_a_chunk_waiting_at_an_action_node_once_its_hint_passes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _ACTION_GRAPH_YAML})
    assert minted.status_code == 201, minted.text
    deliver_view = next(n for n in minted.json()["nodes"] if n["name"] == "deliver")
    assert (deliver_view["action"], deliver_view["action_args"]) == ("land", {})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "7"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert deliver.action == "land"
    # Stand at `deliver`, waiting, as a pass that answered `pending` with a hint leaves it.
    cast(IWriteChunkMovementRepository, hub.services.chunks.movement).record_transition(
        transition_id="tr_test_deliver",
        chunk_id=chunk_id,
        from_node_id=build_node_id,
        to_node_id=deliver.node_id,
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    hint = hub.clock.now() + timedelta(minutes=5)
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(s.hub_node_poll).values(
                chunk_id=chunk_id,
                node_id=deliver.node_id,
                epoch=1,
                polled_at=hub.clock.now(),
                next_poll_at=hint,
                tenant_id=default_tenant(hub.engine),
            )
        )

    # Short of its hint, a tick takes no turn for it: it waits at its next poll.
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) is None
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["pending"]["next_poll_at"] == iso_utc(hint)

    hub.clock.advance(timedelta(minutes=5))
    assert hub.turn_outcome(chunk_id) is not None


_GARDEN_GRAPH_YAML = """
name: garden-delivery
entry: deliver
nodes:
  deliver:
    executor: hub
    action: garden-deliver
    with: {delta: delta, proposals: [docket, more]}
    judgement:
      choices:
        recorded: {description: Recorded., to: done}
        invalid: {description: Invalid., to: done}
        failure: {description: Failed., to: done}
"""


@pytest.mark.component
def test_a_nodes_action_and_its_arguments_round_trip_through_the_store_and_the_graph_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _GARDEN_GRAPH_YAML})
    assert minted.status_code == 201, minted.text
    graph = hub.services.graphs.get(minted.json()["graph_id"])
    assert graph is not None
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert (deliver.action, dict(deliver.action_args)) == (
        "garden-deliver",
        {"delta": ("delta",), "proposals": ("docket", "more")},
    )
    view = hub.client.get(f"/api/graphs/{graph.graph_id}").json()
    assert view["nodes"][0]["action_args"] == {"delta": ["delta"], "proposals": ["docket", "more"]}
