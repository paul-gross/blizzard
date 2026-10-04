"""A chunk told as a lifetime trace and a work trace (unit tier) — ``StepFacts`` built directly, no store."""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from types import SimpleNamespace
from typing import Any

import pytest

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import (
    ChunkRole,
    chunk_span_id,
    chunk_trace_id,
    instant_text,
    lifetime_context,
    lifetime_trace_id,
    step_root,
)
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.hub.domain.chunk.model import UsageFact
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.observability.tracing import attributes as attr
from blizzard.hub.domain.observability.tracing.assembly import assemble_step
from blizzard.hub.domain.observability.tracing.chunk_spans import (
    ChunkOutcome,
    _step_extents,
    assemble_completion,
    assemble_lifetime,
    assemble_work,
    chunk_end,
)
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import (
    StepFacts,
    TracedBounce,
    TracedChunkCompletion,
    TracedChunkStop,
    TracedDecision,
    TracedDecisionResolution,
    TracedEpochOwner,
    TracedEscalation,
    TracedPause,
    TracedPromotion,
    TracedRequeue,
    TracedRestart,
    TracedRouteCreation,
    TracedTransition,
)
from blizzard.hub.domain.observability.tracing.steps import identify_steps
from blizzard.hub.domain.observability.tracing.window import ClosedStep, FinishedChunk, assemble_window, select_window
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit


def _usage(epoch: int, at: int) -> UsageFact:
    return UsageFact(
        node_id="g1-build",
        epoch=epoch,
        kind="spawn",
        model="claude-x",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_create_tokens=5,
        cost_usd=0.5,
        recorded_at=fx.at(at),
    )


def _done(**extra: Any) -> StepFacts:
    """build, review (bounced), build again, review, then the terminal: one chunk, four steps."""
    parts = fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 35), fx.runner_epoch(3, 60), fx.runner_epoch(4, 85))
    return fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        routes_created=(TracedRouteCreation(fx.at(6)),),
        transitions=(
            fx.to("g1", "review", 30, 1),
            fx.to("g1", "build", 55, 2),
            fx.to("g1", "review", 80, 3),
            TracedTransition(4, fx.at(100), "g1", RESERVED_TERMINAL),
        ),
        bounces=(TracedBounce(2, "conflict", fx.at(50)),),
        usage=(_usage(1, 20), _usage(3, 70)),
        work_refs=("acme#42",),
        **{**parts, **extra},
    )


def _all_spans(facts: StepFacts) -> list[FinishedSpan]:
    steps = [s for s in identify_steps(facts) if s.close is not None]
    return (
        [span for step in steps for span in assemble_step(facts, step, identify_steps(facts))]
        + list(assemble_work(facts))
        + list(assemble_lifetime(facts))
    )


def _named(spans: tuple[FinishedSpan, ...], name: str) -> FinishedSpan:
    return next(s for s in spans if s.name == name)


def test_a_bounced_chunk_is_a_work_trace_under_one_work_root() -> None:
    facts = _done()
    steps = [s for step in identify_steps(facts) for s in assemble_step(facts, step, identify_steps(facts))]
    (work,) = assemble_work(facts)

    assert {s.context.trace_id for s in [*steps, work]} == {chunk_trace_id("ch_1")}
    assert len({(s.context.trace_id, s.context.span_id) for s in [*steps, work]}) == len(steps) + 1
    assert (work.name, work.parent_span_id, work.context.span_id) == ("chunk work", None, chunk_span_id("ch_1"))
    roots = [s for s in steps if s.name.startswith("step ")]
    assert len(roots) == 4
    assert {r.parent_span_id for r in roots} == {work.context.span_id}


def test_the_work_root_starts_at_the_first_step_and_ends_at_the_finish_with_totals_but_no_waits() -> None:
    facts = _done()
    (work,) = assemble_work(facts)
    first = assemble_step(facts, identify_steps(facts)[0], identify_steps(facts))[0]

    assert (work.start, work.end) == (first.start, fx.at(100))
    assert work.start > fx.at(0)
    assert work.attributes[attr.CHUNK_OUTCOME] == "done"
    assert work.attributes[attr.CHUNK_STEPS] == 4
    assert work.attributes[attr.CHUNK_COST_USD] == 1.0
    assert attr.CHUNK_BACKLOG_MS not in work.attributes
    assert work.service_name is None
    assert [(link.context, link.attributes[attr.LINK_REASON]) for link in work.links] == [
        (lifetime_context("ch_1"), "lifetime")
    ]


def test_a_chunk_that_took_no_step_has_a_lifetime_trace_and_no_work_trace() -> None:
    facts = fx.make_facts(chunk_stopped=(TracedChunkStop(fx.at(40)),))

    assert assemble_work(facts) == ()
    assert [s.name for s in assemble_lifetime(facts)] == ["chunk", "backlog wait"]


def test_the_lifetime_trace_is_its_own_trace_under_the_chunk_service() -> None:
    spans = assemble_lifetime(_done())

    assert {s.context.trace_id for s in spans} == {lifetime_trace_id("ch_1")} != {chunk_trace_id("ch_1")}
    assert len({s.context.span_id for s in spans}) == len(spans)
    assert {s.service_name for s in spans} == {"blizzard-chunk"}
    root = spans[0]
    assert (root.name, root.parent_span_id, root.context) == ("chunk", None, lifetime_context("ch_1"))
    assert {s.parent_span_id for s in spans[1:]} == {root.context.span_id}


def test_the_lifetime_trace_has_a_span_per_step_named_for_its_node_and_linking_to_the_step_root() -> None:
    facts = _done()
    steps = identify_steps(facts)
    spans = assemble_lifetime(facts)
    told = [s for s in spans if attr.STEP_KIND in s.attributes]
    roots = [assemble_step(facts, step, identify_steps(facts))[0] for step in steps]

    assert [s.name for s in told] == ["build", "review", "build", "review"]
    assert [(s.start, s.end) for s in told] == [
        (fx.at(5), fx.at(30)),
        (fx.at(35), fx.at(55)),
        (fx.at(60), fx.at(80)),
        (fx.at(85), fx.at(100)),
    ]
    assert [[(link.context, link.attributes[attr.LINK_REASON]) for link in s.links] for s in told] == [
        [(step_root(step.key), "work")] for step in steps
    ]
    first = told[0].attributes
    assert first[attr.STEP_KIND] == "step"
    assert first[shared.NODE_NAME] == "build"
    assert first[shared.STEP_EPOCH] == 1
    assert first[attr.STEP_OUTCOME] == roots[0].attributes[attr.STEP_OUTCOME]
    assert first[attr.STEP_INPUT_TOKENS] == roots[0].attributes[attr.STEP_INPUT_TOKENS] == 100
    assert first[attr.STEP_COST_USD] == roots[0].attributes[attr.STEP_COST_USD] == 0.5
    assert first[attr.WAIT_QUEUE_MS] == roots[0].attributes[attr.WAIT_QUEUE_MS] == 1000
    assert first[attr.WAIT_CLAIM_MS] == roots[0].attributes[attr.WAIT_CLAIM_MS] == 4000
    for name in (attr.WAIT_ASK_MS, attr.WAIT_PAUSE_MS, attr.WAIT_PICKUP_MS):
        assert first[name] == roots[0].attributes[name]


def test_the_lifetime_trace_leaves_no_gap_where_the_work_trace_accounts_for_the_time() -> None:
    spans = assemble_lifetime(_done())
    backlog = _named(spans, "backlog wait")
    first_step = next(s for s in spans if s.attributes.get(attr.STEP_KIND) == "step")

    assert backlog.end == first_step.start == fx.at(5)


def test_a_lifetime_wait_and_a_lifetime_step_never_overlap() -> None:
    spans = assemble_lifetime(_done(pauses=(TracedPause("p1", True, fx.at(3)), TracedPause("p2", False, fx.at(8)))))
    covered = sorted((s.start, s.end, s.name) for s in spans[1:])

    assert all(earlier[1] <= later[0] for earlier, later in pairwise(covered))


def test_a_wait_that_reaches_into_a_step_span_is_clipped_to_what_no_step_shows() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        routes_created=(TracedRouteCreation(fx.at(6)), TracedRouteCreation(fx.at(300))),
        escalations=(TracedEscalation(1, fx.at(40)),),
        transitions=(TracedTransition(2, fx.at(400), "g1", RESERVED_TERMINAL),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 310)),
    )

    spans = assemble_lifetime(facts)
    steps = [s for s in spans if s.attributes.get(attr.STEP_KIND) == "step"]
    waits = [s for s in spans if s.name.endswith(" wait")]

    assert len(steps) == 2
    assert all(w.end <= s.start or w.start >= s.end for w in waits for s in steps)
    assert all(a.end <= b.start for a, b in pairwise(sorted(steps, key=lambda s: s.start)))
    escalation = _named(spans, "escalation wait")
    assert (escalation.start, escalation.end) == (fx.at(40), fx.at(310))
    assert steps[1].start == fx.at(310)


def test_an_escalation_a_restart_releases_is_a_wait_and_the_next_step_queues_from_the_restart() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        routes_created=(TracedRouteCreation(fx.at(6)), TracedRouteCreation(fx.at(300))),
        escalations=(TracedEscalation(1, fx.at(40)),),
        restarts=(TracedRestart(2, fx.at(250), "g1", "g1-build"),),
        transitions=(TracedTransition(2, fx.at(400), "g1", RESERVED_TERMINAL),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 310)),
    )

    spans = assemble_lifetime(facts)
    wait = _named(spans, "escalation wait")
    lifetime_steps = sorted((s for s in spans if s.attributes.get(attr.STEP_KIND) == "step"), key=lambda s: s.start)
    second = assemble_step(facts, identify_steps(facts)[1], identify_steps(facts))

    assert (wait.start, wait.end) == (fx.at(40), fx.at(250))
    assert lifetime_steps[1].start == fx.at(250)
    assert _named(second, "queue wait").start == fx.at(250)


def test_a_step_span_that_would_run_past_the_next_steps_start_ends_there() -> None:
    def summary(started: int, ended: int, closed: int) -> Any:
        return SimpleNamespace(started_at=fx.at(started), ended_at=fx.at(ended), closed_at=fx.at(closed), intervals=())

    extents = _step_extents([summary(10, 50, 90), summary(60, 70, 70)], [])

    assert extents == [(fx.at(10), fx.at(60)), (fx.at(60), fx.at(70))]


def test_a_gate_is_a_lifetime_span_of_kind_gate() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        decisions=(TracedDecision("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(TracedDecisionResolution("d1", fx.at(40), choice="approve"),),
        transitions=(
            fx.to("g1", "build", 50, 2, decision_id="d1", choice_name="approve"),
            TracedTransition(2, fx.at(100), "g1", RESERVED_TERMINAL),
        ),
        **fx.runner_epoch(1, 10),
    )

    gate = next(s for s in assemble_lifetime(facts) if s.attributes.get(attr.STEP_KIND) == "gate")

    gate_key = next(step.key for step in identify_steps(facts) if step.decision_id == "d1")

    assert gate.name == "gate"
    assert [(link.context, link.attributes[attr.LINK_REASON]) for link in gate.links] == [(step_root(gate_key), "work")]
    assert gate.end == fx.at(50)
    assert gate.attributes[attr.WAIT_PICKUP_MS] == 10000


def test_step_roots_keep_their_links_and_reasons() -> None:
    facts = _done()
    steps = identify_steps(facts)
    roots = [assemble_step(facts, s, identify_steps(facts))[0] for s in steps]

    assert [r.links[0].attributes[attr.LINK_REASON] for r in roots[1:]] == ["next", "bounce", "next"]
    assert [r.links[0].context for r in roots[1:]] == [step_root(s.key) for s in steps[:-1]]
    assert not roots[0].links


def test_the_lifetime_root_runs_from_ingest_to_done_with_the_chunks_totals() -> None:
    spans = assemble_lifetime(_done())
    chunk, backlog = spans[0], spans[-1]

    assert (chunk.start, chunk.end) == (fx.at(0), fx.at(100))
    assert chunk.attributes[attr.CHUNK_OUTCOME] == "done"
    assert chunk.attributes[attr.CHUNK_BACKLOG_MS] == 5000
    assert chunk.attributes[attr.CHUNK_ACTIVE_MS] == 95000
    assert chunk.attributes[attr.CHUNK_STEPS] == 4
    assert chunk.attributes[attr.CHUNK_BOUNCES] == 1
    assert chunk.attributes[attr.CHUNK_INPUT_TOKENS] == 200
    assert chunk.attributes[attr.CHUNK_COST_USD] == 1.0
    assert chunk.attributes[shared.CHUNK_WORK_REFS] == ("acme#42",)
    assert (backlog.name, backlog.start, backlog.end) == ("backlog wait", fx.at(0), fx.at(5))
    assert backlog.parent_span_id == chunk.context.span_id


def test_the_lifetime_spans_are_derived_not_random() -> None:
    first, second = assemble_lifetime(_done()), assemble_lifetime(_done())

    assert first == second
    assert first[-1].context == lifetime_context("ch_1", ChunkRole.BACKLOG, instant_text(fx.at(0)))


def test_a_stopped_chunk_keeps_its_outcome_and_a_later_completion_is_a_marker() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        chunk_stopped=(TracedChunkStop(fx.at(40)),),
        chunk_completed=(TracedChunkCompletion(fx.at(90)),),
        **fx.runner_epoch(1, 10),
    )

    chunk = assemble_lifetime(facts)[0]
    (marker,) = assemble_completion(facts)

    assert chunk.end == fx.at(40)
    assert chunk.attributes[attr.CHUNK_OUTCOME] == "stopped"
    assert marker.name == "chunk completed"
    assert (marker.start, marker.end) == (fx.at(90), fx.at(90))
    assert marker.parent_span_id == chunk.context.span_id
    assert marker.attributes[attr.CHUNK_OUTCOME] == "done"
    assert marker.context.trace_id == chunk.context.trace_id
    assert marker.service_name == "blizzard-chunk"


def test_a_completion_that_ties_the_stop_decides_the_chunk_and_leaves_no_marker() -> None:
    facts = fx.make_facts(
        chunk_stopped=(TracedChunkStop(fx.at(40)),), chunk_completed=(TracedChunkCompletion(fx.at(40)),)
    )

    end = chunk_end(facts)

    assert end is not None
    assert end.outcome is ChunkOutcome.DONE
    with pytest.raises(ValueError, match="not hand-completed"):
        assemble_completion(facts)


def test_an_unfinished_chunk_is_refused() -> None:
    facts = fx.make_facts(promotions=(TracedPromotion(fx.at(5)),))

    assert chunk_end(facts) is None
    with pytest.raises(ValueError, match="unfinished"):
        assemble_lifetime(facts)


def test_an_escalation_wait_covers_the_gap_to_the_requeue() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        routes_created=(TracedRouteCreation(fx.at(6)), TracedRouteCreation(fx.at(300))),
        escalations=(TracedEscalation(1, fx.at(40)),),
        requeues=(TracedRequeue(fx.at(300)),),
        transitions=(TracedTransition(2, fx.at(400), "g1", RESERVED_TERMINAL),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 310)),
    )

    spans = assemble_lifetime(facts)
    wait = _named(spans, "escalation wait")
    second = assemble_step(facts, identify_steps(facts)[1], identify_steps(facts))

    assert (wait.start, wait.end) == (fx.at(40), fx.at(300))
    assert wait.parent_span_id == spans[0].context.span_id
    assert _named(second, "queue wait").start == fx.at(300)


def test_an_escalation_still_open_at_the_stop_ends_there() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        escalations=(TracedEscalation(1, fx.at(40)),),
        chunk_stopped=(TracedChunkStop(fx.at(70)),),
        **fx.runner_epoch(1, 10),
    )

    wait = _named(assemble_lifetime(facts), "escalation wait")

    assert (wait.start, wait.end) == (fx.at(40), fx.at(70))


def test_a_pause_while_unclaimed_is_a_wait_and_a_pause_inside_a_step_is_not() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        pauses=(
            TracedPause("p1", True, fx.at(7)),
            TracedPause("p2", False, fx.at(9)),
            TracedPause("p3", True, fx.at(20)),
            TracedPause("p4", False, fx.at(25)),
        ),
        transitions=(TracedTransition(1, fx.at(60), "g1", RESERVED_TERMINAL),),
        **fx.runner_epoch(1, 10),
    )

    waits = [s for s in assemble_lifetime(facts) if s.name == "pause wait"]

    assert [(w.start, w.end) for w in waits] == [(fx.at(7), fx.at(9))]
    assert waits[0].context == lifetime_context("ch_1", ChunkRole.PAUSE, instant_text(fx.at(7)))


def test_a_pause_a_restart_closes_the_step_under_is_a_wait_from_the_close_to_the_resume() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        pauses=(TracedPause("p1", True, fx.at(20)), TracedPause("p2", False, fx.at(50))),
        restarts=(TracedRestart(2, fx.at(30), "g1", "g1-build"),),
        transitions=(TracedTransition(3, fx.at(80), "g1", RESERVED_TERMINAL),),
        **fx.merge(
            fx.runner_epoch(1, 10), {"epoch_owners": (TracedEpochOwner(2, None, fx.at(30)),)}, fx.runner_epoch(3, 55)
        ),
    )

    waits = [s for s in assemble_lifetime(facts) if s.name == "pause wait"]
    pause_child = [
        s for s in assemble_step(facts, identify_steps(facts)[0], identify_steps(facts)) if s.name == "pause"
    ]

    assert [(w.start, w.end) for w in waits] == [(fx.at(30), fx.at(50))]
    assert waits[0].context == lifetime_context("ch_1", ChunkRole.PAUSE, instant_text(fx.at(30)))
    assert [(p.start, p.end) for p in pause_child] == [(fx.at(20), fx.at(30))]


def test_a_pause_while_a_gate_holds_the_route_is_a_wait_only_after_the_gate_closes() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        decisions=(TracedDecision("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(TracedDecisionResolution("d1", fx.at(40), choice="approve"),),
        pauses=(TracedPause("p1", True, fx.at(30)), TracedPause("p2", False, fx.at(520))),
        transitions=(
            fx.to("g1", "build", 500, 2, decision_id="d1", choice_name="approve"),
            TracedTransition(2, fx.at(600), "g1", RESERVED_TERMINAL),
        ),
        **fx.runner_epoch(1, 10),
    )

    waits = [s for s in assemble_lifetime(facts) if s.name == "pause wait"]

    gate = next(s for s in assemble_lifetime(facts) if s.attributes.get(attr.STEP_KIND) == "gate")

    assert (gate.start, gate.end) == (fx.at(20), fx.at(500))
    assert [(w.start, w.end) for w in waits] == [(fx.at(500), fx.at(520))]
    assert waits[0].context == lifetime_context("ch_1", ChunkRole.PAUSE, instant_text(fx.at(500)))


def test_a_pause_inside_the_backlog_leaves_the_backlog_wait_alone() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(20)),),
        pauses=(TracedPause("p1", True, fx.at(5)), TracedPause("p2", False, fx.at(30))),
        transitions=(TracedTransition(1, fx.at(80), "g1", RESERVED_TERMINAL),),
        **fx.runner_epoch(1, 35),
    )

    spans = assemble_lifetime(facts)
    backlog = _named(spans, "backlog wait")
    waits = [s for s in spans if s.name == "pause wait"]

    assert (backlog.start, backlog.end) == (fx.at(0), fx.at(20))
    assert [(w.start, w.end) for w in waits] == [(fx.at(20), fx.at(30))]


def test_a_pause_inside_an_escalation_wait_leaves_it_alone() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        pauses=(TracedPause("p1", True, fx.at(50)), TracedPause("p2", False, fx.at(100))),
        escalations=(TracedEscalation(1, fx.at(40)),),
        requeues=(TracedRequeue(fx.at(200)),),
        transitions=(TracedTransition(2, fx.at(400), "g1", RESERVED_TERMINAL),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 210)),
    )

    spans = assemble_lifetime(facts)

    assert (_named(spans, "escalation wait").start, _named(spans, "escalation wait").end) == (fx.at(40), fx.at(200))
    assert not [s for s in spans if s.name == "pause wait"]


def test_a_chunk_never_promoted_rests_in_the_backlog_until_it_ends() -> None:
    facts = fx.make_facts(chunk_stopped=(TracedChunkStop(fx.at(40)),))

    chunk, backlog = assemble_lifetime(facts)

    assert (backlog.start, backlog.end) == (fx.at(0), fx.at(40))
    assert chunk.attributes[attr.CHUNK_BACKLOG_MS] == 40000
    assert chunk.attributes[attr.CHUNK_ACTIVE_MS] == 0


def test_instants_in_span_ids_are_utc_microseconds() -> None:
    assert instant_text(datetime(2026, 1, 1, 0, 0, 5, 250000, tzinfo=fx.T0.tzinfo)) == "2026-01-01T00:00:05.250000Z"


def _window(facts: StepFacts, since: CursorKey, limit: int = 100) -> Any:
    return select_window([facts], since, fx.at(10_000), None, limit, None)


def test_a_sweep_tells_each_chunk_span_exactly_once() -> None:
    facts = _done()
    first = _window(facts, CursorKey.opening(fx.at(0)))

    assert [type(i) for i in first.items].count(ClosedStep) == 4
    assert len(first.finished_chunks()) == 1
    again = _window(facts, first.position)
    assert again.items == ()


def test_a_chunk_span_is_told_before_a_later_completion_marker_and_neither_is_told_twice() -> None:
    facts = fx.make_facts(
        promotions=(TracedPromotion(fx.at(5)),),
        chunk_stopped=(TracedChunkStop(fx.at(40)),),
        chunk_completed=(TracedChunkCompletion(fx.at(90)),),
        **fx.runner_epoch(1, 10),
    )

    told = _window(facts, CursorKey.opening(fx.at(0)), limit=100)
    kinds = [(i.completion if isinstance(i, FinishedChunk) else None) for i in told.items]

    assert kinds.count(False) == 1
    assert kinds.count(True) == 1
    assert _window(facts, told.position).items == ()
    # A pass that stopped after the chunk was told, before the completion, tells only the marker afterwards.
    chunk_key = CursorKey.chunk_finished(fx.at(40), "ch_1")
    later = _window(facts, chunk_key)
    assert [i.completion for i in later.finished_chunks()] == [True]


def test_a_replay_and_the_sweep_tell_the_same_ids() -> None:
    facts = _done(pauses=(TracedPause("p1", True, fx.at(31)), TracedPause("p2", False, fx.at(33))))
    swept = assemble_window(_window(facts, CursorKey.opening(fx.at(0))))
    replayed = assemble_window(_window(facts, CursorKey.opening(fx.at(100)), limit=100))

    def ids(spans: list[FinishedSpan]) -> set[tuple[int, int, int | None]]:
        return {(s.context.trace_id, s.context.span_id, s.parent_span_id) for s in spans}

    def whole(spans: tuple[FinishedSpan, ...]) -> list[FinishedSpan]:
        """What a finished chunk is told as: its lifetime trace and its work root, not the steps the sweep told earlier."""
        return [s for s in spans if s.context.trace_id == lifetime_trace_id("ch_1") or s.name == "chunk work"]

    assert ids(whole(swept)) == ids(whole(replayed))
    assert {s.name for s in swept} >= {"chunk", "chunk work", "build", "backlog wait", "pause wait"}
    roots = {(s.name, s.context) for s in swept if s.name in ("chunk", "chunk work")}
    assert {c.span_id for _, c in roots} == {chunk_span_id("ch_1"), lifetime_context("ch_1").span_id}
    assert {c.trace_id for _, c in roots} == {chunk_trace_id("ch_1"), lifetime_trace_id("ch_1")}


def test_a_sweep_tells_both_traces_of_a_chunk_once() -> None:
    facts = _done()
    swept = assemble_window(_window(facts, CursorKey.opening(fx.at(0))))
    contexts = [(s.context.trace_id, s.context.span_id) for s in swept]

    assert len(contexts) == len(set(contexts))
    assert [s.name for s in swept].count("chunk work") == [s.name for s in swept].count("chunk") == 1
    assert {s.context.trace_id for s in swept} == {chunk_trace_id("ch_1"), lifetime_trace_id("ch_1")}


def test_a_chunk_without_an_ingest_instant_is_not_a_finished_item() -> None:
    facts = _done(minted_at=None)

    assert _window(facts, CursorKey.opening(fx.at(0))).finished_chunks() == ()
