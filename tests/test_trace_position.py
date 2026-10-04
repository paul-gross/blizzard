"""Step identification and the position fold (unit tier) — ``StepFacts`` built directly, no store."""

from __future__ import annotations

import pytest

from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.trace_ids import StepKey
from blizzard.hub.domain.observability.tracing.facts import (
    StepFacts,
    TracedChunkCompletion,
    TracedChunkStop,
    TracedDecision,
    TracedDecisionResolution,
    TracedEpochOwner,
    TracedEscalation,
    TracedLease,
    TracedMigration,
    TracedRequeue,
    TracedRestart,
    TracedRouteRelease,
)
from blizzard.hub.domain.observability.tracing.position import position_at
from blizzard.hub.domain.observability.tracing.steps import PrecededBy, StepKind, StepOutcome, identify_steps
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit


def test_runner_step_starts_at_earliest_lease_and_owner_is_the_runner() -> None:
    facts = fx.make_facts(
        lease_facts=(TracedLease(1, fx.at(20)), TracedLease(1, fx.at(10))),
        epoch_owners=(TracedEpochOwner(1, "hub", fx.at(5)),),
    )
    (step,) = identify_steps(facts)
    assert step.kind is StepKind.RUNNER
    assert step.runner_id == "hub"
    assert step.start == fx.at(10)
    assert step.key == StepKey.attempt("ch_1", 1)
    assert step.close is None


def test_hub_step_starts_at_latest_placement_and_requeue() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "gate", 5, 1),),
        requeues=(TracedRequeue(fx.at(8)),),
        **fx.merge(fx.runner_epoch(2, 12, runner=None)),
    )
    (step,) = identify_steps(facts)
    assert step.kind is StepKind.HUB
    assert step.start == fx.at(8)
    assert step.position.node_name == "gate"
    # the requeue that started the step is not also what preceded it
    assert step.preceded_by is None


@pytest.mark.parametrize(
    ("extra", "outcome"),
    [
        ({"transitions": (fx.to("g1", "review", 30, 1),)}, StepOutcome.TRANSITIONED),
        (
            {"decisions": (TracedDecision("d1", "g1-review", 1, fx.at(30), imposed_by_runner_id="r-1"),)},
            StepOutcome.GATED,
        ),
        (
            {"migrations": (TracedMigration(1, fx.at(30), "g1", "g2", source=None),)},
            StepOutcome.MIGRATED,
        ),
        ({"escalations": (TracedEscalation(1, fx.at(30)),)}, StepOutcome.ESCALATED),
        ({"route_released": (TracedRouteRelease(fx.at(30)),)}, StepOutcome.RELEASED),
        ({"chunk_stopped": (TracedChunkStop(fx.at(30)),)}, StepOutcome.STOPPED),
        ({"chunk_completed": (TracedChunkCompletion(fx.at(30)),)}, StepOutcome.COMPLETED),
        ({"epoch_owners": (TracedEpochOwner(2, "r-2", fx.at(30)),)}, StepOutcome.SUPERSEDED),
    ],
)
def test_runner_step_closing_table(extra: dict[str, tuple[object, ...]], outcome: StepOutcome) -> None:
    facts = fx.make_facts(**fx.merge(fx.runner_epoch(1, 10), extra))
    step = identify_steps(facts)[0]
    assert step.close is not None
    assert step.close.outcome is outcome
    assert step.close.at == fx.at(30)


def test_restart_sourced_migration_does_not_close_the_step() -> None:
    migration = TracedMigration(1, fx.at(30), "g1", "g2", source=MigrationSource.RESTART)
    (step,) = identify_steps(fx.make_facts(migrations=(migration,), **fx.runner_epoch(1, 10)))
    assert step.close is None


def test_transition_with_a_decision_does_not_close_a_runner_step() -> None:
    transition = fx.to("g1", "review", 30, 1, decision_id="d1")
    (step,) = identify_steps(fx.make_facts(transitions=(transition,), **fx.runner_epoch(1, 10)))
    assert step.close is None


def test_release_with_a_later_fact_at_its_epoch_is_not_a_release() -> None:
    facts = fx.make_facts(
        route_released=(TracedRouteRelease(fx.at(20)),),
        transitions=(fx.to("g1", "review", 30, 1),),
        **fx.runner_epoch(1, 10),
    )
    assert identify_steps(facts)[0].close.outcome is StepOutcome.TRANSITIONED  # type: ignore[union-attr]


def test_earliest_closing_fact_wins_and_table_order_breaks_a_tie() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "review", 30, 1),),
        escalations=(TracedEscalation(1, fx.at(30)),),
        chunk_stopped=(TracedChunkStop(fx.at(25)),),
        **fx.runner_epoch(1, 10),
    )
    assert identify_steps(facts)[0].close.outcome is StepOutcome.STOPPED  # type: ignore[union-attr]
    tied = fx.make_facts(
        transitions=(fx.to("g1", "review", 30, 1),),
        escalations=(TracedEscalation(1, fx.at(30)),),
        **fx.runner_epoch(1, 10),
    )
    assert identify_steps(tied)[0].close.outcome is StepOutcome.TRANSITIONED  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("extra", "outcome"),
    [
        ({"transitions": (fx.to("g1", "review", 60, 1, decision_id="d1"),)}, StepOutcome.DECIDED),
        (
            {"migrations": (TracedMigration(1, fx.at(60), "g1", "g2", decision_id="d1"),)},
            StepOutcome.MIGRATED,
        ),
        ({"escalations": (TracedEscalation(1, fx.at(60), decision_id="d1"),)}, StepOutcome.ESCALATED),
        (
            {"restarts": (TracedRestart(2, fx.at(60), "g1", "g1-build", decision_id="d1"),)},
            StepOutcome.RESTARTED,
        ),
        ({"chunk_stopped": (TracedChunkStop(fx.at(60)),)}, StepOutcome.STOPPED),
        ({"chunk_completed": (TracedChunkCompletion(fx.at(60)),)}, StepOutcome.COMPLETED),
        ({"epoch_owners": (TracedEpochOwner(2, "r-2", fx.at(60)),)}, StepOutcome.SUPERSEDED),
    ],
)
def test_gate_closing_table(extra: dict[str, tuple[object, ...]], outcome: StepOutcome) -> None:
    decision = TracedDecision("d1", "g1-gate", 1, fx.at(20))
    facts = fx.make_facts(decisions=(decision,), **extra)
    (step,) = identify_steps(facts)
    assert step.kind is StepKind.GATE
    assert step.close is not None
    assert step.close.outcome is outcome


def test_two_gates_on_one_epoch_are_two_steps_and_a_resolved_gate_keeps_both_instants() -> None:
    facts = fx.make_facts(
        decisions=(
            TracedDecision("d1", "g1-review", 1, fx.at(20), imposed_by_runner_id="r-1"),
            TracedDecision("d2", "g1-gate", 1, fx.at(30)),
        ),
        decision_resolutions=(TracedDecisionResolution("d2", fx.at(40)),),
        transitions=(fx.to("g1", "build", 500, 2, decision_id="d2"),),
        **fx.runner_epoch(1, 10),
    )
    steps = identify_steps(facts)
    gates = [s for s in steps if s.kind is StepKind.GATE]
    assert [g.decision_id for g in gates] == ["d1", "d2"]
    assert gates[0].close is None
    assert gates[1].resolved_at == fx.at(40)
    assert gates[1].close is not None
    assert gates[1].close.at == fx.at(500)
    assert gates[1].key == StepKey.gate("ch_1", 1, "d2")


def test_fence_only_epochs_make_no_step_and_surface_as_preceded_by() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "review", 15, 1),),
        restarts=(TracedRestart(2, fx.at(18), "g1", "g1-review"),),
        epoch_owners=(
            TracedEpochOwner(1, "r-1", fx.at(9)),
            TracedEpochOwner(2, None, fx.at(18)),
            TracedEpochOwner(3, "r-1", fx.at(25)),
            TracedEpochOwner(4, "r-1", fx.at(28)),
            TracedEpochOwner(5, "r-1", fx.at(39)),
        ),
        lease_facts=(TracedLease(1, fx.at(10)), TracedLease(5, fx.at(40))),
    )
    first, second = identify_steps(facts)
    assert first.preceded_by is None
    # the nearest fence-only event wins: epoch 4's released claim, not epoch 2's restart
    assert second.preceded_by is PrecededBy.RELEASED_CLAIM
    only_restart = fx.make_facts(
        restarts=(TracedRestart(2, fx.at(18), "g1", "g1-review"),),
        epoch_owners=(
            TracedEpochOwner(1, "r-1", fx.at(9)),
            TracedEpochOwner(2, None, fx.at(18)),
            TracedEpochOwner(3, "r-1", fx.at(39)),
        ),
        lease_facts=(TracedLease(1, fx.at(10)), TracedLease(3, fx.at(40))),
    )
    assert identify_steps(only_restart)[1].preceded_by is PrecededBy.RESTART


def test_a_requeue_between_steps_is_preceded_by_requeue() -> None:
    facts = fx.make_facts(
        requeues=(TracedRequeue(fx.at(20)),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 30)),
    )
    assert identify_steps(facts)[1].preceded_by is PrecededBy.REQUEUE


def test_position_visits_count_by_name_across_graph_versions() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "review", 10, 1), fx.to("g1", "build", 20, 2)),
        migrations=(TracedMigration(3, fx.at(30), "g1", "g2", from_node_id="g1-build", landed_node_id=None),),
    )
    assert position_at(facts, fx.at(5)).visit == 1  # the entry placement is the first visit
    at_build_again = position_at(facts, fx.at(25))
    assert (at_build_again.node_name, at_build_again.visit) == ("build", 2)
    landed = position_at(facts, fx.at(35))
    # name-matched landing in the other graph version, third arrival at "build"
    assert (landed.graph_id, landed.node_id, landed.visit) == ("g2", "g2-build", 3)


def test_migration_without_a_name_match_lands_on_the_entry_node() -> None:
    odd = fx.graph("g3", "start", "other")
    facts = StepFacts(
        chunk_id="ch_1",
        graphs={**fx.GRAPHS, "g3": odd},
        transitions=(fx.to("g1", "review", 10, 1),),
        migrations=(TracedMigration(2, fx.at(20), "g1", "g3", from_node_id="g1-review"),),
    )
    assert position_at(facts, fx.at(25)).node_id == "g3-start"


def test_restart_moves_the_fold() -> None:
    facts = fx.make_facts(restarts=(TracedRestart(1, fx.at(10), "g2", "g2-review", from_graph_id="g1"),))
    position = position_at(facts, fx.at(12))
    assert (position.graph_id, position.node_name) == ("g2", "review")
    assert position_at(facts, fx.at(5)).graph_id == "g1"


def test_the_pin_is_ignored_once_the_chunk_has_moved() -> None:
    moved = StepFacts(
        chunk_id="ch_1",
        graphs=fx.GRAPHS,
        pin_graph_id="g2",
        transitions=(fx.to("g1", "review", 10, 1),),
        migrations=(TracedMigration(2, fx.at(50), "g1", "g2", from_node_id="g1-review"),),
    )
    assert position_at(moved, fx.at(5)).graph_id == "g1"
    assert position_at(moved, fx.at(20)).graph_id == "g1"
    unmoved = StepFacts(chunk_id="ch_1", graphs=fx.GRAPHS, pin_graph_id="g2")
    assert position_at(unmoved, fx.at(5)).graph_id == "g2"


def test_a_step_keeps_its_historical_position_after_a_later_migration() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "review", 10, 1),),
        migrations=(TracedMigration(2, fx.at(50), "g1", "g2", from_node_id="g1-review"),),
        **fx.runner_epoch(1, 12),
    )
    (step,) = identify_steps(facts)
    assert (step.position.graph_id, step.position.node_name) == ("g1", "review")


def test_gate_position_uses_its_decisions_node() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "gate", 20, 1),),
        decisions=(TracedDecision("d1", "g1-gate", 1, fx.at(20)),),
    )
    (step,) = identify_steps(facts)
    assert (step.position.node_name, step.position.visit) == ("gate", 1)
