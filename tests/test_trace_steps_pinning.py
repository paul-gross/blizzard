"""Step closing, hub start, preceded-by and starting-graph pinning (unit tier) — ``StepFacts`` built directly."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from blizzard.foundation.trace_ids import StepKey
from blizzard.hub.domain.tracing.facts import (
    StepFacts,
    TracedChunkCompletion,
    TracedChunkStop,
    TracedDecision,
    TracedEpochOwner,
    TracedEscalation,
    TracedMigration,
    TracedRequeue,
    TracedRestart,
    TracedRouteRelease,
    TracedTransition,
)
from blizzard.hub.domain.tracing.position import (
    Position,
    _starting_graph_id,
    arrivals_with_entry,
    movement_arrivals,
    position_of_node,
)
from blizzard.hub.domain.tracing.steps import (
    NodeStep,
    PrecededBy,
    StepKind,
    StepOutcome,
    _gate_close,
    _hub_start,
    _runner_close,
    _terminal_candidates,
    _with_preceded_by,
)
from blizzard.hub.domain.work import MigrationSource
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

US = timedelta(microseconds=1)
START = fx.at(10)
TIE = fx.at(30)

RUNNER_CLOSERS: list[tuple[str, object, StepOutcome]] = [
    ("transitions", fx.to("g1", "review", 30, 1), StepOutcome.TRANSITIONED),
    ("decisions", TracedDecision("d1", "g1-review", 1, TIE, imposed_by_runner_id="r-1"), StepOutcome.GATED),
    ("migrations", TracedMigration(1, TIE, "g1", "g2", landed_node_id="g2-build"), StepOutcome.MIGRATED),
    ("escalations", TracedEscalation(1, TIE), StepOutcome.ESCALATED),
    ("route_released", TracedRouteRelease(TIE), StepOutcome.RELEASED),
    ("chunk_stopped", TracedChunkStop(TIE), StepOutcome.STOPPED),
    ("chunk_completed", TracedChunkCompletion(TIE), StepOutcome.COMPLETED),
    ("epoch_owners", TracedEpochOwner(2, "r-2", TIE), StepOutcome.SUPERSEDED),
]


def _facts_of(closers: list[tuple[str, object, StepOutcome]]) -> StepFacts:
    return fx.make_facts(**fx.merge(*({table: (fact,)} for table, fact, _ in closers)))


@pytest.mark.parametrize("first", range(len(RUNNER_CLOSERS)))
def test_runner_close_table_order_breaks_an_exact_tie(first: int) -> None:
    tied = RUNNER_CLOSERS[first:]
    close = _runner_close(_facts_of(tied), 1, START)
    assert close is not None
    assert close.outcome is tied[0][2]
    assert close.at == TIE
    assert close.fact is tied[0][1]


@pytest.mark.parametrize("index", range(len(RUNNER_CLOSERS)))
def test_runner_close_earlier_fact_beats_table_order(index: int) -> None:
    table, fact, outcome = RUNNER_CLOSERS[index]
    stopped = TracedChunkStop(TIE + US) if table != "chunk_stopped" else TracedChunkCompletion(TIE + US)
    extra = "chunk_stopped" if table != "chunk_stopped" else "chunk_completed"
    facts = fx.make_facts(**fx.merge({table: (fact,)}, {extra: (stopped,)}))
    close = _runner_close(facts, 1, START)
    assert close is not None
    assert (close.outcome, close.at, close.fact) == (outcome, TIE, fact)


@pytest.mark.parametrize(
    ("table", "fact"),
    [
        ("transitions", fx.to("g1", "review", 30, 2)),
        ("transitions", fx.to("g1", "review", 30, 1, decision_id="d1")),
        ("decisions", TracedDecision("d1", "g1-review", 1, TIE)),
        ("decisions", TracedDecision("d1", "g1-review", 2, TIE, imposed_by_runner_id="r-1")),
        ("migrations", TracedMigration(2, TIE, "g1", "g2", landed_node_id="g2-build")),
        ("migrations", TracedMigration(1, TIE, "g1", "g2", landed_node_id="g2-build", source=MigrationSource.RESTART)),
        ("escalations", TracedEscalation(2, TIE)),
        ("epoch_owners", TracedEpochOwner(1, "r-2", TIE)),
        ("epoch_owners", TracedEpochOwner(0, "r-2", TIE)),
    ],
)
def test_runner_close_ignores_facts_not_closing_this_epoch(table: str, fact: object) -> None:
    assert _runner_close(fx.make_facts(**{table: (fact,)}), 1, START) is None


@pytest.mark.parametrize(
    ("table", "fact", "closes"),
    [
        ("chunk_stopped", TracedChunkStop(START), False),
        ("chunk_stopped", TracedChunkStop(START + US), True),
        ("chunk_completed", TracedChunkCompletion(START), False),
        ("chunk_completed", TracedChunkCompletion(START + US), True),
        ("route_released", TracedRouteRelease(START), False),
        ("route_released", TracedRouteRelease(START + US), True),
        ("epoch_owners", TracedEpochOwner(2, "r-2", START - US), False),
        ("epoch_owners", TracedEpochOwner(2, "r-2", START), True),
    ],
)
def test_runner_close_start_bounds(table: str, fact: object, closes: bool) -> None:
    close = _runner_close(fx.make_facts(**{table: (fact,)}), 1, START)
    assert (close is not None and close.fact is fact) is closes


@pytest.mark.parametrize(
    ("later", "released"),
    [
        ({"transitions": (fx.to("g1", "review", 30, 1, decision_id="d1"),)}, False),
        ({"decisions": (TracedDecision("d1", "g1-review", 1, TIE),)}, False),
        ({"migrations": (TracedMigration(1, TIE, "g1", "g2", landed_node_id="g2-build"),)}, False),
        ({"escalations": (TracedEscalation(1, TIE),)}, False),
        ({"restarts": (TracedRestart(1, TIE, "g1", "g1-build"),)}, False),
        ({"restarts": (TracedRestart(2, TIE, "g1", "g1-build"),)}, True),
        ({"restarts": (TracedRestart(1, fx.at(20), "g1", "g1-build"),)}, True),
    ],
)
def test_runner_close_release_is_void_behind_a_later_fact_at_its_epoch(
    later: dict[str, tuple[object, ...]], released: bool
) -> None:
    release = TracedRouteRelease(fx.at(20))
    close = _runner_close(fx.make_facts(route_released=(release,), **later), 1, START)
    assert (close is not None and close.fact is release) is released


def test_terminal_candidates_supersede_at_the_earliest_higher_owner() -> None:
    first = TracedEpochOwner(3, "r-3", fx.at(40))
    facts = fx.make_facts(epoch_owners=(TracedEpochOwner(2, "r-2", fx.at(50)), first, TracedEpochOwner(1, "r", TIE)))
    (candidate,) = _terminal_candidates(facts, START, 1)
    assert (candidate.at, candidate.order, candidate.outcome, candidate.fact) == (
        fx.at(40),
        7,
        StepOutcome.SUPERSEDED,
        first,
    )


def test_terminal_candidates_orders() -> None:
    stopped, completed = TracedChunkStop(TIE), TracedChunkCompletion(TIE)
    owner = TracedEpochOwner(2, "r-2", TIE)
    facts = fx.make_facts(chunk_stopped=(stopped,), chunk_completed=(completed,), epoch_owners=(owner,))
    found = [(c.order, c.outcome, c.fact) for c in _terminal_candidates(facts, START, 1)]
    assert found == [
        (5, StepOutcome.STOPPED, stopped),
        (6, StepOutcome.COMPLETED, completed),
        (7, StepOutcome.SUPERSEDED, owner),
    ]


GATE = TracedDecision("d1", "g1-gate", 1, fx.at(20))

GATE_CLOSERS: list[tuple[str, object, StepOutcome]] = [
    ("transitions", fx.to("g1", "build", 30, 2, decision_id="d1"), StepOutcome.DECIDED),
    (
        "migrations",
        TracedMigration(2, TIE, "g1", "g2", landed_node_id="g2-build", decision_id="d1"),
        StepOutcome.MIGRATED,
    ),
    ("escalations", TracedEscalation(2, TIE, decision_id="d1"), StepOutcome.ESCALATED),
    ("restarts", TracedRestart(2, TIE, "g1", "g1-build", decision_id="d1"), StepOutcome.RESTARTED),
    ("chunk_stopped", TracedChunkStop(TIE), StepOutcome.STOPPED),
]


@pytest.mark.parametrize("first", range(len(GATE_CLOSERS)))
def test_gate_close_table_order_breaks_an_exact_tie(first: int) -> None:
    tied = GATE_CLOSERS[first:]
    close = _gate_close(_facts_of(tied), GATE)
    assert close is not None
    assert (close.outcome, close.at, close.fact) == (tied[0][2], TIE, tied[0][1])


@pytest.mark.parametrize(
    ("table", "fact"),
    [
        ("transitions", fx.to("g1", "build", 30, 2, decision_id="d2")),
        ("transitions", fx.to("g1", "build", 30, 1)),
        ("migrations", TracedMigration(2, TIE, "g1", "g2", landed_node_id="g2-build", decision_id="d2")),
        ("escalations", TracedEscalation(1, TIE)),
        ("restarts", TracedRestart(2, TIE, "g1", "g1-build", decision_id="d2")),
        ("chunk_stopped", TracedChunkStop(GATE.submitted_at)),
        ("epoch_owners", TracedEpochOwner(2, "r-2", GATE.submitted_at - US)),
    ],
)
def test_gate_close_ignores_facts_not_carrying_its_decision(table: str, fact: object) -> None:
    assert _gate_close(fx.make_facts(**{table: (fact,)}), GATE) is None


def test_gate_close_supersede_bound_is_inclusive_of_submission() -> None:
    owner = TracedEpochOwner(2, "r-2", GATE.submitted_at)
    close = _gate_close(fx.make_facts(epoch_owners=(owner,)), GATE)
    assert close is not None
    assert (close.outcome, close.at, close.fact) == (StepOutcome.SUPERSEDED, GATE.submitted_at, owner)


MINTED = fx.at(10)


@pytest.mark.parametrize(
    ("requeue", "start"),
    [
        (MINTED - US, MINTED - US),
        (MINTED, MINTED),
        (MINTED + US, fx.at(5)),
        (fx.at(5), fx.at(5)),
        (fx.at(5) + US, fx.at(5) + US),
        (fx.at(4), fx.at(5)),
    ],
)
def test_hub_start_requeue_bounds(requeue: datetime, start: datetime) -> None:
    facts = fx.make_facts(transitions=(fx.to("g1", "gate", 5, 1),), requeues=(TracedRequeue(requeue),))
    assert _hub_start(facts, MINTED) == start


@pytest.mark.parametrize(
    ("placed", "start"),
    [(MINTED - US, MINTED - US), (MINTED, fx.at(1)), (MINTED + US, fx.at(1))],
)
def test_hub_start_placement_must_precede_the_lease(placed: datetime, start: datetime) -> None:
    gate = TracedTransition(epoch=1, recorded_at=placed, graph_id="g1", to_node_id="g1-gate")
    facts = fx.make_facts(transitions=(fx.to("g1", "review", 1, 1), gate))
    assert _hub_start(facts, MINTED) == start


def test_hub_start_without_placement_is_the_lease_and_ignores_requeues() -> None:
    facts = fx.make_facts(requeues=(TracedRequeue(MINTED - US),))
    assert _hub_start(facts, MINTED) == MINTED


def test_hub_start_takes_the_latest_placement_and_latest_requeue() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "review", 3, 1), fx.to("g1", "gate", 5, 1)),
        requeues=(TracedRequeue(fx.at(6)), TracedRequeue(fx.at(8)), TracedRequeue(fx.at(7))),
    )
    assert _hub_start(facts, MINTED) == fx.at(8)


POS = Position("g1", "g1-build", "build", 1)


def _step(epoch: int, start: datetime, kind: StepKind = StepKind.RUNNER) -> NodeStep:
    return NodeStep(
        kind=kind, key=StepKey.attempt("ch_1", epoch), epoch=epoch, runner_id="r-1", start=start, position=POS
    )


@pytest.mark.parametrize(
    ("extra", "preceded"),
    [
        ({}, None),
        ({"requeues": (TracedRequeue(fx.at(19)),)}, PrecededBy.REQUEUE),
        ({"requeues": (TracedRequeue(fx.at(20)),)}, None),
        ({"requeues": (TracedRequeue(fx.at(10)),)}, None),
        ({"requeues": (TracedRequeue(fx.at(10) + US),)}, PrecededBy.REQUEUE),
        ({"epoch_owners": (TracedEpochOwner(5, "r-1", fx.at(15)),)}, PrecededBy.RELEASED_CLAIM),
        ({"epoch_owners": (TracedEpochOwner(2, "r-1", fx.at(15)),)}, None),
        ({"epoch_owners": (TracedEpochOwner(1, "r-1", fx.at(15)),)}, None),
        (
            {
                "epoch_owners": (TracedEpochOwner(5, None, fx.at(15)),),
                "restarts": (TracedRestart(5, fx.at(15), "g1", "g1-build"),),
            },
            PrecededBy.RESTART,
        ),
        (
            {
                "epoch_owners": (TracedEpochOwner(5, None, fx.at(15)),),
                "migrations": (TracedMigration(5, fx.at(15), "g1", "g2", source=MigrationSource.RESTART),),
            },
            PrecededBy.RESTART,
        ),
        (
            {
                "epoch_owners": (TracedEpochOwner(5, None, fx.at(15)),),
                "migrations": (TracedMigration(5, fx.at(15), "g1", "g2", landed_node_id="g2-build"),),
            },
            PrecededBy.RELEASED_CLAIM,
        ),
        (
            {
                "epoch_owners": (TracedEpochOwner(5, "r-1", fx.at(15)),),
                "decisions": (TracedDecision("d1", "g1-gate", 5, fx.at(12)),),
            },
            None,
        ),
        (
            {"epoch_owners": (TracedEpochOwner(5, "r-1", fx.at(15)),), "requeues": (TracedRequeue(fx.at(16)),)},
            PrecededBy.REQUEUE,
        ),
        (
            {"epoch_owners": (TracedEpochOwner(5, "r-1", fx.at(16)),), "requeues": (TracedRequeue(fx.at(15)),)},
            PrecededBy.RELEASED_CLAIM,
        ),
    ],
)
def test_with_preceded_by_nearest_fence_event_between_steps(
    extra: dict[str, tuple[object, ...]], preceded: PrecededBy | None
) -> None:
    steps = [_step(1, fx.at(10)), _step(2, fx.at(20))]
    first, second = _with_preceded_by(fx.make_facts(**extra), steps)
    assert first.preceded_by is None
    assert second.preceded_by is preceded


@pytest.mark.parametrize(
    ("requeue", "preceded"),
    [(fx.at(9), PrecededBy.REQUEUE), (fx.at(10), None), (fx.at(0), PrecededBy.REQUEUE)],
)
def test_with_preceded_by_first_step_has_no_lower_bound(requeue: datetime, preceded: PrecededBy | None) -> None:
    (step,) = _with_preceded_by(fx.make_facts(requeues=(TracedRequeue(requeue),)), [_step(1, fx.at(10))])
    assert step.preceded_by is preceded


def test_with_preceded_by_gate_epoch_is_not_a_step_epoch() -> None:
    facts = fx.make_facts(epoch_owners=(TracedEpochOwner(7, "r-1", fx.at(15)),))
    steps = [_step(1, fx.at(10)), _step(7, fx.at(12), StepKind.GATE), _step(2, fx.at(20))]
    out = _with_preceded_by(facts, steps)
    assert [s.preceded_by for s in out] == [None, None, PrecededBy.RELEASED_CLAIM]
    assert [s.start for s in out] == [fx.at(10), fx.at(12), fx.at(20)]


def _starting(facts: StepFacts) -> str:
    return _starting_graph_id(facts, movement_arrivals(facts))


@pytest.mark.parametrize(
    ("extra", "graph_id"),
    [
        ({}, "g2"),
        ({"migrations": (TracedMigration(1, fx.at(5), "g1", "g2", landed_node_id="g2-build"),)}, "g1"),
        (
            {
                "migrations": (TracedMigration(1, fx.at(5), "g1", "g2", landed_node_id="g2-build"),),
                "transitions": (fx.to("g2", "review", 5, 1),),
            },
            "g2",
        ),
        (
            {
                "migrations": (TracedMigration(1, fx.at(5), "g1", "g2", landed_node_id="g2-build"),),
                "restarts": (TracedRestart(1, fx.at(5), "g2", "g2-build"),),
            },
            "g1",
        ),
        ({"restarts": (TracedRestart(1, fx.at(5), "g2", "g2-build", from_graph_id="g1"),)}, "g1"),
        ({"restarts": (TracedRestart(1, fx.at(5), "g1", "g1-build"),)}, "g1"),
        (
            {
                "restarts": (TracedRestart(1, fx.at(5), "g1", "g1-build"),),
                "migrations": (TracedMigration(2, fx.at(5), "g1", "g2", landed_node_id="g2-build"),),
            },
            "g1",
        ),
        (
            {
                "restarts": (TracedRestart(2, fx.at(5), "g1", "g1-build"),),
                "migrations": (TracedMigration(1, fx.at(5), "g2", "g1", landed_node_id="g1-build"),),
            },
            "g2",
        ),
    ],
)
def test_starting_graph_id_reads_the_first_movement(extra: dict[str, tuple[object, ...]], graph_id: str) -> None:
    assert _starting(fx.make_facts(pin_graph_id="g2", **extra)) == graph_id


def test_starting_graph_id_without_movement_needs_a_pin() -> None:
    with pytest.raises(LookupError, match="pin_graph_id"):
        _starting(fx.make_facts(pin_graph_id=None))


def test_arrivals_with_entry_raises_for_a_missing_starting_graph() -> None:
    with pytest.raises(LookupError, match="'g9'"):
        arrivals_with_entry(fx.make_facts(pin_graph_id="g9"))


def test_position_of_node_resolves_a_gate_in_a_non_starting_graph() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "gate", 5, 1), fx.to("g1", "gate", 8, 1), fx.to("g1", "gate", 30, 1))
    )
    assert position_of_node(facts, "g2-gate", fx.at(20)) == Position("g2", "g2-gate", "gate", 2)
    assert position_of_node(fx.make_facts(), "g2-gate", fx.at(20)) == Position("g2", "g2-gate", "gate", 1)


def test_position_of_node_raises_for_an_unknown_node() -> None:
    with pytest.raises(LookupError, match="'g9-gate'"):
        position_of_node(fx.make_facts(), "g9-gate", fx.at(20))
