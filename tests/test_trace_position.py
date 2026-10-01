"""Step identification and the position fold (unit tier) — ``StepFacts`` built directly, no store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.trace_ids import StepKey
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.tracing.facts import (
    ChunkCompletedRecord,
    ChunkStoppedRecord,
    DecisionRecord,
    DecisionResolutionRecord,
    EpochOwnerRecord,
    EscalationRecord,
    LeaseRecord,
    MigrationRecord,
    RequeueRecord,
    RestartRecord,
    RouteReleasedRecord,
    StepFacts,
    TransitionRecord,
)
from blizzard.hub.domain.tracing.position import position_at
from blizzard.hub.domain.tracing.steps import PrecededBy, StepKind, StepOutcome, identify_steps
from blizzard.hub.domain.work import MigrationSource

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _node(graph_id: str, name: str, executor: Executor = Executor.RUNNER) -> Node:
    return Node(
        node_id=f"{graph_id}-{name}",
        graph_id=graph_id,
        name=name,
        executor=executor,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def _graph(graph_id: str, *names: str) -> Graph:
    return Graph(
        graph_id=graph_id,
        name="flow",
        entry_node_id=f"{graph_id}-{names[0]}",
        nodes=[_node(graph_id, n) for n in names],
        edges=[],
        created_at=_T0,
    )


G1 = _graph("g1", "build", "review", "gate")
G2 = _graph("g2", "build", "review", "gate")
GRAPHS = {"g1": G1, "g2": G2}


def _facts(**kwargs: object) -> StepFacts:
    return StepFacts(chunk_id="ch_1", graphs=GRAPHS, pin_graph_id="g1", **kwargs)  # type: ignore[arg-type]


def _to(graph: str, name: str, at: int, epoch: int, **kw: str | None) -> TransitionRecord:
    return TransitionRecord(epoch=epoch, recorded_at=_at(at), graph_id=graph, to_node_id=f"{graph}-{name}", **kw)


def _runner_epoch(epoch: int, at: int, runner: str | None = "r-1") -> dict[str, tuple[object, ...]]:
    return {
        "lease_facts": (LeaseRecord(epoch, _at(at)),),
        "epoch_owners": (EpochOwnerRecord(epoch, runner, _at(at - 1)),),
    }


def _merge(*parts: dict[str, tuple[object, ...]]) -> dict[str, tuple[object, ...]]:
    out: dict[str, tuple[object, ...]] = {}
    for part in parts:
        for key, value in part.items():
            out[key] = out.get(key, ()) + value
    return out


def test_runner_step_starts_at_earliest_lease_and_owner_is_the_runner() -> None:
    facts = _facts(
        lease_facts=(LeaseRecord(1, _at(20)), LeaseRecord(1, _at(10))),
        epoch_owners=(EpochOwnerRecord(1, "hub", _at(5)),),
    )
    (step,) = identify_steps(facts)
    assert step.kind is StepKind.RUNNER
    assert step.runner_id == "hub"
    assert step.start == _at(10)
    assert step.key == StepKey.attempt("ch_1", 1)
    assert step.close is None


def test_hub_step_starts_at_latest_placement_and_requeue() -> None:
    facts = _facts(
        transitions=(_to("g1", "gate", 5, 1),),
        requeues=(RequeueRecord(_at(8)),),
        **_merge(_runner_epoch(2, 12, runner=None)),
    )
    (step,) = identify_steps(facts)
    assert step.kind is StepKind.HUB
    assert step.start == _at(8)
    assert step.position.node_name == "gate"
    # the requeue that started the step is not also what preceded it
    assert step.preceded_by is None


@pytest.mark.parametrize(
    ("extra", "outcome"),
    [
        ({"transitions": (_to("g1", "review", 30, 1),)}, StepOutcome.TRANSITIONED),
        (
            {"decisions": (DecisionRecord("d1", "g1-review", 1, _at(30), imposed_by_runner_id="r-1"),)},
            StepOutcome.GATED,
        ),
        (
            {"migrations": (MigrationRecord(1, _at(30), "g1", "g2", source=None),)},
            StepOutcome.MIGRATED,
        ),
        ({"escalations": (EscalationRecord(1, _at(30)),)}, StepOutcome.ESCALATED),
        ({"route_released": (RouteReleasedRecord(_at(30)),)}, StepOutcome.RELEASED),
        ({"chunk_stopped": (ChunkStoppedRecord(_at(30)),)}, StepOutcome.STOPPED),
        ({"chunk_completed": (ChunkCompletedRecord(_at(30)),)}, StepOutcome.COMPLETED),
        ({"epoch_owners": (EpochOwnerRecord(2, "r-2", _at(30)),)}, StepOutcome.SUPERSEDED),
    ],
)
def test_runner_step_closing_table(extra: dict[str, tuple[object, ...]], outcome: StepOutcome) -> None:
    facts = _facts(**_merge(_runner_epoch(1, 10), extra))
    step = identify_steps(facts)[0]
    assert step.close is not None
    assert step.close.outcome is outcome
    assert step.close.at == _at(30)


def test_restart_sourced_migration_does_not_close_the_step() -> None:
    migration = MigrationRecord(1, _at(30), "g1", "g2", source=MigrationSource.RESTART)
    (step,) = identify_steps(_facts(migrations=(migration,), **_runner_epoch(1, 10)))
    assert step.close is None


def test_transition_with_a_decision_does_not_close_a_runner_step() -> None:
    transition = _to("g1", "review", 30, 1, decision_id="d1")
    (step,) = identify_steps(_facts(transitions=(transition,), **_runner_epoch(1, 10)))
    assert step.close is None


def test_release_with_a_later_fact_at_its_epoch_is_not_a_release() -> None:
    facts = _facts(
        route_released=(RouteReleasedRecord(_at(20)),),
        transitions=(_to("g1", "review", 30, 1),),
        **_runner_epoch(1, 10),
    )
    assert identify_steps(facts)[0].close.outcome is StepOutcome.TRANSITIONED  # type: ignore[union-attr]


def test_earliest_closing_fact_wins_and_table_order_breaks_a_tie() -> None:
    facts = _facts(
        transitions=(_to("g1", "review", 30, 1),),
        escalations=(EscalationRecord(1, _at(30)),),
        chunk_stopped=(ChunkStoppedRecord(_at(25)),),
        **_runner_epoch(1, 10),
    )
    assert identify_steps(facts)[0].close.outcome is StepOutcome.STOPPED  # type: ignore[union-attr]
    tied = _facts(
        transitions=(_to("g1", "review", 30, 1),),
        escalations=(EscalationRecord(1, _at(30)),),
        **_runner_epoch(1, 10),
    )
    assert identify_steps(tied)[0].close.outcome is StepOutcome.TRANSITIONED  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("extra", "outcome"),
    [
        ({"transitions": (_to("g1", "review", 60, 1, decision_id="d1"),)}, StepOutcome.DECIDED),
        (
            {"migrations": (MigrationRecord(1, _at(60), "g1", "g2", decision_id="d1"),)},
            StepOutcome.MIGRATED,
        ),
        ({"escalations": (EscalationRecord(1, _at(60), decision_id="d1"),)}, StepOutcome.ESCALATED),
        (
            {"restarts": (RestartRecord(2, _at(60), "g1", "g1-build", decision_id="d1"),)},
            StepOutcome.RESTARTED,
        ),
        ({"chunk_stopped": (ChunkStoppedRecord(_at(60)),)}, StepOutcome.STOPPED),
        ({"chunk_completed": (ChunkCompletedRecord(_at(60)),)}, StepOutcome.COMPLETED),
        ({"epoch_owners": (EpochOwnerRecord(2, "r-2", _at(60)),)}, StepOutcome.SUPERSEDED),
    ],
)
def test_gate_closing_table(extra: dict[str, tuple[object, ...]], outcome: StepOutcome) -> None:
    decision = DecisionRecord("d1", "g1-gate", 1, _at(20))
    facts = _facts(decisions=(decision,), **extra)
    (step,) = identify_steps(facts)
    assert step.kind is StepKind.GATE
    assert step.close is not None
    assert step.close.outcome is outcome


def test_two_gates_on_one_epoch_are_two_steps_and_a_resolved_gate_keeps_both_instants() -> None:
    facts = _facts(
        decisions=(
            DecisionRecord("d1", "g1-review", 1, _at(20), imposed_by_runner_id="r-1"),
            DecisionRecord("d2", "g1-gate", 1, _at(30)),
        ),
        decision_resolutions=(DecisionResolutionRecord("d2", _at(40)),),
        transitions=(_to("g1", "build", 500, 2, decision_id="d2"),),
        **_runner_epoch(1, 10),
    )
    steps = identify_steps(facts)
    gates = [s for s in steps if s.kind is StepKind.GATE]
    assert [g.decision_id for g in gates] == ["d1", "d2"]
    assert gates[0].close is None
    assert gates[1].resolved_at == _at(40)
    assert gates[1].close is not None
    assert gates[1].close.at == _at(500)
    assert gates[1].key == StepKey.gate("ch_1", 1, "d2")


def test_fence_only_epochs_make_no_step_and_surface_as_preceded_by() -> None:
    facts = _facts(
        transitions=(_to("g1", "review", 15, 1),),
        restarts=(RestartRecord(2, _at(18), "g1", "g1-review"),),
        epoch_owners=(
            EpochOwnerRecord(1, "r-1", _at(9)),
            EpochOwnerRecord(2, None, _at(18)),
            EpochOwnerRecord(3, "r-1", _at(25)),
            EpochOwnerRecord(4, "r-1", _at(28)),
            EpochOwnerRecord(5, "r-1", _at(39)),
        ),
        lease_facts=(LeaseRecord(1, _at(10)), LeaseRecord(5, _at(40))),
    )
    first, second = identify_steps(facts)
    assert first.preceded_by is None
    # the nearest fence-only event wins: epoch 4's released claim, not epoch 2's restart
    assert second.preceded_by is PrecededBy.RELEASED_CLAIM
    only_restart = _facts(
        restarts=(RestartRecord(2, _at(18), "g1", "g1-review"),),
        epoch_owners=(
            EpochOwnerRecord(1, "r-1", _at(9)),
            EpochOwnerRecord(2, None, _at(18)),
            EpochOwnerRecord(3, "r-1", _at(39)),
        ),
        lease_facts=(LeaseRecord(1, _at(10)), LeaseRecord(3, _at(40))),
    )
    assert identify_steps(only_restart)[1].preceded_by is PrecededBy.RESTART


def test_a_requeue_between_steps_is_preceded_by_requeue() -> None:
    facts = _facts(
        requeues=(RequeueRecord(_at(20)),),
        **_merge(_runner_epoch(1, 10), _runner_epoch(2, 30)),
    )
    assert identify_steps(facts)[1].preceded_by is PrecededBy.REQUEUE


def test_position_visits_count_by_name_across_graph_versions() -> None:
    facts = _facts(
        transitions=(_to("g1", "review", 10, 1), _to("g1", "build", 20, 2)),
        migrations=(MigrationRecord(3, _at(30), "g1", "g2", from_node_id="g1-build", landed_node_id=None),),
    )
    assert position_at(facts, _at(5)).visit == 1  # the entry placement is the first visit
    at_build_again = position_at(facts, _at(25))
    assert (at_build_again.node_name, at_build_again.visit) == ("build", 2)
    landed = position_at(facts, _at(35))
    # name-matched landing in the other graph version, third arrival at "build"
    assert (landed.graph_id, landed.node_id, landed.visit) == ("g2", "g2-build", 3)


def test_migration_without_a_name_match_lands_on_the_entry_node() -> None:
    odd = _graph("g3", "start", "other")
    facts = StepFacts(
        chunk_id="ch_1",
        graphs={**GRAPHS, "g3": odd},
        transitions=(_to("g1", "review", 10, 1),),
        migrations=(MigrationRecord(2, _at(20), "g1", "g3", from_node_id="g1-review"),),
    )
    assert position_at(facts, _at(25)).node_id == "g3-start"


def test_restart_moves_the_fold() -> None:
    facts = _facts(restarts=(RestartRecord(1, _at(10), "g2", "g2-review", from_graph_id="g1"),))
    position = position_at(facts, _at(12))
    assert (position.graph_id, position.node_name) == ("g2", "review")
    assert position_at(facts, _at(5)).graph_id == "g1"


def test_the_pin_is_ignored_once_the_chunk_has_moved() -> None:
    moved = StepFacts(
        chunk_id="ch_1",
        graphs=GRAPHS,
        pin_graph_id="g2",
        transitions=(_to("g1", "review", 10, 1),),
        migrations=(MigrationRecord(2, _at(50), "g1", "g2", from_node_id="g1-review"),),
    )
    assert position_at(moved, _at(5)).graph_id == "g1"
    assert position_at(moved, _at(20)).graph_id == "g1"
    unmoved = StepFacts(chunk_id="ch_1", graphs=GRAPHS, pin_graph_id="g2")
    assert position_at(unmoved, _at(5)).graph_id == "g2"


def test_a_step_keeps_its_historical_position_after_a_later_migration() -> None:
    facts = _facts(
        transitions=(_to("g1", "review", 10, 1),),
        migrations=(MigrationRecord(2, _at(50), "g1", "g2", from_node_id="g1-review"),),
        **_runner_epoch(1, 12),
    )
    (step,) = identify_steps(facts)
    assert (step.position.graph_id, step.position.node_name) == ("g1", "review")


def test_gate_position_uses_its_decisions_node() -> None:
    facts = _facts(
        transitions=(_to("g1", "gate", 20, 1),),
        decisions=(DecisionRecord("d1", "g1-gate", 1, _at(20)),),
    )
    (step,) = identify_steps(facts)
    assert (step.position.node_name, step.position.visit) == ("gate", 1)
