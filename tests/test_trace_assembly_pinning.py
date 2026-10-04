"""Span assembly and window selection (unit tier) — exact boundary instants, ``StepFacts`` built directly."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.hub.domain.tracing import assembly
from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.chunk_spans import assemble_completion, assemble_lifetime, assemble_work
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import (
    StepFacts,
    TracedChunkCompletion,
    TracedChunkStop,
    TracedDecision,
    TracedEpochOwner,
    TracedLease,
    TracedPause,
    TracedPromotion,
    TracedRouteCreation,
)
from blizzard.hub.domain.tracing.steps import StepKind, identify_steps
from blizzard.hub.domain.tracing.summary import StepSummary, summarize_step
from blizzard.hub.domain.tracing.window import (
    ClosedStep,
    FinishedChunk,
    TraceWindow,
    assemble_window,
    select_window,
)
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

US = timedelta(microseconds=1)


def _spans(facts: StepFacts, index: int = 0) -> tuple[FinishedSpan, ...]:
    steps = identify_steps(facts)
    return assemble_step(facts, steps[index], steps)


def _intervals(spans: tuple[FinishedSpan, ...]) -> list[tuple[str, datetime, datetime]]:
    return [(s.name, s.start, s.end) for s in spans[1:]]


# --- runner id of a gate ---------------------------------------------------------------------------------------


def _gate_with_owners(*owners: TracedEpochOwner, imposed: str | None = None) -> StepFacts:
    return fx.make_facts(
        decisions=(TracedDecision("d1", "g1-gate", 1, fx.at(20), imposed_by_runner_id=imposed),),
        transitions=(fx.to("g1", "build", 70, 2, decision_id="d1"),),
        epoch_owners=owners,
        lease_facts=(TracedLease(1, fx.at(10)),),
    )


def _gate_root(facts: StepFacts) -> FinishedSpan:
    steps = identify_steps(facts)
    gate = next(s for s in steps if s.kind is StepKind.GATE)
    return assemble_step(facts, gate, steps)[0]


def test_a_gate_falls_back_to_the_latest_owner_of_its_epoch() -> None:
    facts = _gate_with_owners(
        TracedEpochOwner(1, "r-first", fx.at(9)),
        TracedEpochOwner(1, "r-latest", fx.at(15)),
        TracedEpochOwner(1, "r-middle", fx.at(12)),
        TracedEpochOwner(2, "r-other-epoch", fx.at(60)),
    )
    assert _gate_root(facts).attributes[attr.RUNNER_ID] == "r-latest"


def test_a_gate_latest_owner_wins_by_one_microsecond() -> None:
    facts = _gate_with_owners(
        TracedEpochOwner(1, "r-later", fx.at(9) + US),
        TracedEpochOwner(1, "r-earlier", fx.at(9)),
    )
    assert _gate_root(facts).attributes[attr.RUNNER_ID] == "r-later"


def test_a_gate_with_no_owner_of_its_epoch_has_no_runner() -> None:
    facts = _gate_with_owners(TracedEpochOwner(2, "r-other-epoch", fx.at(60)))
    assert attr.RUNNER_ID not in _gate_root(facts).attributes


def test_a_gate_imposed_by_a_runner_keeps_that_runner_over_the_owners() -> None:
    facts = _gate_with_owners(TracedEpochOwner(1, "r-owner", fx.at(9)), imposed="r-9")
    assert _gate_root(facts).attributes[attr.RUNNER_ID] == "r-9"


# --- queue and claim -------------------------------------------------------------------------------------------


def _first_step(created: datetime, **extra: Any) -> StepFacts:
    return fx.make_facts(
        routes_created=(TracedRouteCreation(created),),
        transitions=(fx.to("g1", "review", 40, 1),),
        **fx.merge(fx.runner_epoch(1, 10), extra),
    )


def test_a_route_created_exactly_at_the_step_start_is_claimed() -> None:
    assert _intervals(_spans(_first_step(fx.at(10)))) == [
        ("queue wait", fx.at(10), fx.at(10)),
        ("claim", fx.at(10), fx.at(10)),
    ]


def test_a_route_created_one_microsecond_before_the_step_start_is_claimed() -> None:
    start = fx.at(10) - US
    assert _intervals(_spans(_first_step(start))) == [
        ("queue wait", start, start),
        ("claim", start, fx.at(10)),
    ]


def test_a_route_created_one_microsecond_after_the_step_start_is_not_claimed() -> None:
    assert _intervals(_spans(_first_step(fx.at(10) + US))) == []


def _second_step(*created: datetime) -> StepFacts:
    return fx.make_facts(
        routes_created=tuple(TracedRouteCreation(c) for c in created),
        transitions=(fx.to("g1", "review", 40, 1), fx.to("g1", "gate", 90, 2)),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
    )


def test_a_route_created_exactly_at_the_previous_start_belongs_to_the_previous_step() -> None:
    assert _intervals(_spans(_second_step(fx.at(10)), 1)) == []


def test_a_route_created_one_microsecond_before_the_previous_start_belongs_to_neither() -> None:
    assert _intervals(_spans(_second_step(fx.at(10) - US), 1)) == []


def test_a_route_created_one_microsecond_after_the_previous_start_is_claimed_by_the_next_step() -> None:
    created = fx.at(10) + US
    assert _intervals(_spans(_second_step(created), 1)) == [
        ("queue wait", created, created),
        ("claim", created, fx.at(50)),
    ]


def test_a_route_created_exactly_at_the_step_start_is_claimed_by_the_next_step() -> None:
    assert _intervals(_spans(_second_step(fx.at(50)), 1)) == [
        ("queue wait", fx.at(50), fx.at(50)),
        ("claim", fx.at(50), fx.at(50)),
    ]


def test_the_latest_eligible_route_is_the_claim() -> None:
    facts = _second_step(fx.at(10), fx.at(20), fx.at(45), fx.at(30), fx.at(50) + US)
    assert _intervals(_spans(facts, 1)) == [
        ("queue wait", fx.at(45), fx.at(45)),
        ("claim", fx.at(45), fx.at(50)),
    ]


def test_the_queue_starts_at_a_promotion_exactly_at_the_claim() -> None:
    facts = _first_step(fx.at(8), promotions=(TracedPromotion(fx.at(8)),))
    assert _intervals(_spans(facts))[0] == ("queue wait", fx.at(8), fx.at(8))


def test_the_queue_starts_at_a_promotion_one_microsecond_before_the_claim() -> None:
    facts = _first_step(fx.at(8), promotions=(TracedPromotion(fx.at(8) - US),))
    assert _intervals(_spans(facts))[0] == ("queue wait", fx.at(8) - US, fx.at(8))


def test_a_promotion_one_microsecond_after_the_claim_does_not_open_the_queue() -> None:
    facts = _first_step(fx.at(8), promotions=(TracedPromotion(fx.at(2)), TracedPromotion(fx.at(8) + US)))
    assert _intervals(_spans(facts))[0] == ("queue wait", fx.at(2), fx.at(8))


def test_a_lifted_pause_exactly_at_the_claim_opens_the_queue() -> None:
    facts = _first_step(
        fx.at(8),
        promotions=(TracedPromotion(fx.at(2)),),
        pauses=(TracedPause("p1", True, fx.at(3)), TracedPause("p2", False, fx.at(8))),
    )
    assert _intervals(_spans(facts))[0] == ("queue wait", fx.at(8), fx.at(8))


# --- pauses ----------------------------------------------------------------------------------------------------


def _paused(*pauses: TracedPause) -> StepFacts:
    return fx.make_facts(pauses=pauses, transitions=(fx.to("g1", "review", 40, 1),), **fx.runner_epoch(1, 10))


@pytest.mark.parametrize(
    ("set_at", "expected"),
    [
        (fx.at(10) - US, []),
        (fx.at(10), [("pause", fx.at(10), fx.at(20))]),
        (fx.at(10) + US, [("pause", fx.at(10) + US, fx.at(20))]),
        (fx.at(40) - US, [("pause", fx.at(40) - US, fx.at(40))]),
        (fx.at(40), [("pause", fx.at(40), fx.at(40))]),
        (fx.at(40) + US, []),
    ],
)
def test_a_pause_is_told_only_when_set_within_the_step(set_at: datetime, expected: list[Any]) -> None:
    facts = _paused(TracedPause("p1", True, set_at), TracedPause("p2", False, fx.at(20)))
    assert _intervals(_spans(facts)) == expected


def test_a_pause_lifted_after_the_step_end_is_cut_at_the_end() -> None:
    facts = _paused(TracedPause("p1", True, fx.at(30)), TracedPause("p2", False, fx.at(40) + US))
    assert _intervals(_spans(facts)) == [("pause", fx.at(30), fx.at(40))]


def test_a_pause_lifted_exactly_at_the_step_end_ends_there() -> None:
    facts = _paused(TracedPause("p1", True, fx.at(30)), TracedPause("p2", False, fx.at(40)))
    assert _intervals(_spans(facts)) == [("pause", fx.at(30), fx.at(40))]


def test_a_lift_at_the_same_instant_as_the_pause_does_not_end_it() -> None:
    facts = _paused(TracedPause("p2", False, fx.at(30)), TracedPause("p1", True, fx.at(30)))
    assert _intervals(_spans(facts)) == [("pause", fx.at(30), fx.at(40))]


def test_a_pause_ends_at_the_first_lift_one_microsecond_after_it() -> None:
    facts = _paused(
        TracedPause("p1", True, fx.at(30)),
        TracedPause("p3", False, fx.at(35)),
        TracedPause("p2", False, fx.at(30) + US),
    )
    root, pause = _spans(facts)
    assert (pause.start, pause.end) == (fx.at(30), fx.at(30) + US)
    assert root.attributes[attr.WAIT_PAUSE_MS] == 0


def test_a_lifted_pause_is_not_itself_a_pause() -> None:
    facts = _paused(TracedPause("p2", False, fx.at(20)))
    assert _intervals(_spans(facts)) == []


# --- summarize_step hand-off -----------------------------------------------------------------------------------


def test_assemble_step_hands_its_threaded_steps_to_summarize_step(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = fx.scenarios()["cost-mixed"]
    steps = identify_steps(facts)
    calls: list[tuple[StepFacts, object, object]] = []

    def spy(f: StepFacts, step: Any, threaded: Any = None) -> StepSummary:
        calls.append((f, step, threaded))
        return summarize_step(f, step, threaded)

    monkeypatch.setattr(assembly, "summarize_step", spy)
    assemble_step(facts, steps[1], steps)
    assert len(calls) == 1
    assert calls[0][0] is facts
    assert calls[0][1] is steps[1]
    assert calls[0][2] is steps


# --- window ----------------------------------------------------------------------------------------------------


def _runner_step() -> StepFacts:
    """One closed step, closing at 30s; the chunk is not finished."""
    return fx.scenarios()["runner-step"]


def _key() -> CursorKey:
    return CursorKey(fx.at(30), "ch_1", 1)


def _window(**kw: Any) -> Any:
    args: dict[str, Any] = {
        "since": CursorKey.opening(fx.at(0)),
        "until": fx.at(100),
        "frontier": None,
        "limit": 10,
        "newest": None,
    }
    args.update(kw)
    return select_window([_runner_step()], **args)


def test_a_step_closing_exactly_at_until_is_in_the_window() -> None:
    window = _window(until=fx.at(30))
    assert [i.key for i in window.items] == [_key()]
    assert window.position == _key()


def test_a_step_closing_one_microsecond_after_until_is_out_of_the_window() -> None:
    since = CursorKey.opening(fx.at(0))
    window = _window(until=fx.at(30) - US, since=since)
    assert window.items == ()
    assert window.position == since
    assert assemble_window(window) == ()


def test_a_step_closing_exactly_at_the_frontier_is_held_back() -> None:
    window = _window(frontier=fx.at(30))
    assert window.items == ()
    assert window.position == CursorKey.opening(fx.at(30))


def test_a_step_closing_one_microsecond_before_the_frontier_is_told() -> None:
    window = _window(frontier=fx.at(30) + US)
    assert [i.key for i in window.items] == [_key()]
    assert window.position == CursorKey.opening(fx.at(30) + US)


def test_a_step_at_the_cursor_is_not_told_again() -> None:
    window = _window(since=_key())
    assert window.items == ()
    assert window.position == _key()


def test_a_step_just_after_an_opening_cursor_at_its_instant_is_told() -> None:
    window = _window(since=CursorKey.opening(fx.at(30)))
    assert [i.key for i in window.items] == [_key()]


def test_a_step_before_a_past_cursor_at_its_instant_is_not_told() -> None:
    assert _window(since=CursorKey.past(fx.at(30))).items == ()


def test_the_newest_closing_fact_moves_the_cursor_past_it() -> None:
    window = _window(newest=fx.at(60))
    assert [i.key for i in window.items] == [_key()]
    assert window.position == CursorKey.past(fx.at(60))


def test_a_newest_behind_the_cursor_never_moves_it_back() -> None:
    since = CursorKey.opening(fx.at(80))
    window = _window(since=since, newest=fx.at(60))
    assert window.items == ()
    assert window.position == since


def test_a_truncated_window_stops_at_its_last_told_item_despite_a_frontier() -> None:
    facts = fx.scenarios()["cost-mixed"]
    window = select_window([facts], CursorKey.opening(fx.at(0)), fx.at(100), fx.at(95), 1, fx.at(99))
    assert [i.key for i in window.items] == [CursorKey(fx.at(40), "ch_1", 1)]
    assert window.position == CursorKey(fx.at(40), "ch_1", 1)


def test_assemble_window_tells_a_closed_step_as_its_assembled_spans() -> None:
    window = _window()
    (item,) = window.items
    assert isinstance(item, ClosedStep)
    assert assemble_window(window) == assemble_step(item.facts, item.step, item.steps)


def test_assemble_window_tells_a_finished_and_a_completed_chunk() -> None:
    facts = fx.make_facts(
        chunk_stopped=(TracedChunkStop(fx.at(30)),),
        chunk_completed=(TracedChunkCompletion(fx.at(60)),),
        **fx.runner_epoch(1, 10),
    )
    finished = FinishedChunk(CursorKey.chunk_finished(fx.at(30), "ch_1"), facts)
    completed = FinishedChunk(CursorKey.chunk_completed(fx.at(60), "ch_1"), facts, completion=True)
    told = assemble_window(TraceWindow((finished, completed), completed.key))
    assert told == (*assemble_work(facts), *assemble_lifetime(facts), *assemble_completion(facts))
