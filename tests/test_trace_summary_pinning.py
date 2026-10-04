"""The step summary's executor, holder and work-source dimensions (unit tier), pinned exactly."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.egress.rows import step_row
from blizzard.hub.domain.tracing.facts import EpochOwnerRecord, StepFacts
from blizzard.hub.domain.tracing.steps import NodeStep, StepKind, identify_steps
from blizzard.hub.domain.tracing.summary import _node_executor, _runner_id, summarize_step
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

_EXPORTED = datetime(2026, 2, 1, tzinfo=UTC)


def _first(facts: StepFacts, kind: StepKind) -> NodeStep:
    return next(s for s in identify_steps(facts) if s.kind is kind and s.close is not None)


def _gate() -> tuple[StepFacts, NodeStep]:
    facts = fx.scenarios()["gate-resolved-late-pickup"]
    return facts, _first(facts, StepKind.GATE)


def test_a_runner_step_on_a_runner_node_is_executed_by_the_runner() -> None:
    facts = fx.scenarios()["runner-step"]
    assert _node_executor(facts, _first(facts, StepKind.RUNNER)) == "runner"


def test_a_hub_step_on_a_hub_node_is_executed_by_the_hub() -> None:
    facts = fx.scenarios()["hub-step"]
    step = _first(facts, StepKind.HUB)
    assert facts.graphs["g1"].node_by_id(step.position.node_id).executor is Executor.HUB  # type: ignore[union-attr]
    assert _node_executor(facts, step) == "hub"


def test_a_gate_is_executed_by_a_human_whatever_its_node() -> None:
    facts, step = _gate()
    assert _node_executor(facts, step) == "human"
    assert _node_executor(replace(facts, graphs={}), step) == "human"


def test_the_nodes_declared_executor_wins_over_the_step_kind() -> None:
    facts = fx.scenarios()["runner-step"]
    step = _first(facts, StepKind.RUNNER)
    hub_g1 = replace(fx.G1, nodes=[fx.node("g1", n.name, Executor.HUB) for n in fx.G1.nodes])
    assert _node_executor(replace(facts, graphs={"g1": hub_g1, "g2": fx.G2}), step) == "hub"


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [("runner-step", StepKind.RUNNER, "runner"), ("hub-step", StepKind.HUB, "hub")],
)
def test_a_missing_graph_falls_back_to_the_step_kind(name: str, kind: StepKind, expected: str) -> None:
    facts = fx.scenarios()[name]
    step = _first(facts, kind)
    assert _node_executor(replace(facts, graphs={}), step) == expected


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [("runner-step", StepKind.RUNNER, "runner"), ("hub-step", StepKind.HUB, "hub")],
)
def test_a_missing_node_falls_back_to_the_step_kind(name: str, kind: StepKind, expected: str) -> None:
    facts = fx.scenarios()[name]
    step = _first(facts, kind)
    moved = replace(step, position=replace(step.position, node_id="g1-absent"))
    assert _node_executor(facts, moved) == expected


def test_a_gate_without_a_runner_reports_its_epochs_latest_owner() -> None:
    facts, step = _gate()
    step = replace(step, runner_id=None)
    owners = (
        EpochOwnerRecord(step.epoch, "r-early", fx.at(1)),
        EpochOwnerRecord(step.epoch, "r-late", fx.at(5)),
        EpochOwnerRecord(step.epoch, "r-middle", fx.at(3)),
        EpochOwnerRecord(step.epoch + 1, "r-other-epoch", fx.at(9)),
    )
    assert _runner_id(replace(facts, epoch_owners=owners), step) == "r-late"


def test_a_gate_with_no_owner_of_its_epoch_has_no_runner() -> None:
    facts, step = _gate()
    step = replace(step, runner_id=None)
    owners = (EpochOwnerRecord(step.epoch + 1, "r-other-epoch", fx.at(9)),)
    assert _runner_id(replace(facts, epoch_owners=owners), step) is None


def test_a_gate_already_holding_a_runner_keeps_it() -> None:
    facts, step = _gate()
    step = replace(step, runner_id="r-held")
    owners = (EpochOwnerRecord(step.epoch, "r-owner", fx.at(5)),)
    assert _runner_id(replace(facts, epoch_owners=owners), step) == "r-held"


@pytest.mark.parametrize("runner", ["r-step", None])
def test_a_non_gate_step_passes_its_runner_through(runner: str | None) -> None:
    facts = fx.scenarios()["runner-step"]
    step = replace(_first(facts, StepKind.RUNNER), runner_id=runner)
    owners = (EpochOwnerRecord(step.epoch, "r-owner", fx.at(50)),)
    assert _runner_id(replace(facts, epoch_owners=owners), step) == runner


def test_work_sources_flow_from_the_facts_to_the_summary_and_the_sources_column() -> None:
    facts = replace(fx.scenarios()["runner-step"], work_sources=("blizzard", "winter"))
    summary = summarize_step(facts, _first(facts, StepKind.RUNNER))
    assert summary.work_sources == ("blizzard", "winter")
    assert step_row(summary, _EXPORTED).sources == ("blizzard", "winter")


def test_no_work_sources_yield_an_empty_sources_column() -> None:
    facts = replace(fx.scenarios()["runner-step"], work_sources=())
    summary = summarize_step(facts, _first(facts, StepKind.RUNNER))
    assert summary.work_sources == ()
    assert step_row(summary, _EXPORTED).sources == ()


def test_a_step_whose_graph_is_missing_is_named_by_its_graph_id() -> None:
    facts = fx.scenarios()["runner-step"]
    step = _first(facts, StepKind.RUNNER)
    summary = summarize_step(replace(facts, graphs={}), step, identify_steps(facts))
    assert summary.graph_id == step.position.graph_id == "g1"
    assert summary.graph_name == "g1"
    assert summary.node_executor == "runner"
