"""A chunk told as one trace (unit tier) — ``StepFacts`` built directly, no store."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import (
    ChunkRole,
    chunk_context,
    chunk_span_id,
    chunk_trace_id,
    instant_text,
    step_root,
)
from blizzard.foundation.trace_spans import SpanRecord
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.chunk_spans import ChunkOutcome, assemble_chunk, assemble_completion, chunk_end
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.tracing.facts import (
    BounceRecord,
    ChunkCompletedRecord,
    ChunkStoppedRecord,
    DecisionRecord,
    DecisionResolutionRecord,
    EpochOwnerRecord,
    EscalationRecord,
    PauseRecord,
    PromotionRecord,
    RequeueRecord,
    RestartRecord,
    RouteCreatedRecord,
    StepFacts,
    TransitionRecord,
)
from blizzard.hub.domain.tracing.steps import identify_steps
from blizzard.hub.domain.tracing.window import ClosedStep, FinishedChunk, assemble_window, select_window
from blizzard.hub.domain.work import UsageFact
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
        promotions=(PromotionRecord(fx.at(5)),),
        routes_created=(RouteCreatedRecord(fx.at(6)),),
        transitions=(
            fx.to("g1", "review", 30, 1),
            fx.to("g1", "build", 55, 2),
            fx.to("g1", "review", 80, 3),
            TransitionRecord(4, fx.at(100), "g1", RESERVED_TERMINAL),
        ),
        bounces=(BounceRecord(2, "conflict", fx.at(50)),),
        usage=(_usage(1, 20), _usage(3, 70)),
        work_refs=("acme#42",),
        **{**parts, **extra},
    )


def _all_spans(facts: StepFacts) -> list[SpanRecord]:
    steps = [s for s in identify_steps(facts) if s.close is not None]
    return [span for step in steps for span in assemble_step(facts, step)] + list(assemble_chunk(facts))


def _named(spans: tuple[SpanRecord, ...], name: str) -> SpanRecord:
    return next(s for s in spans if s.name == name)


def test_a_bounced_chunk_is_one_trace_under_one_chunk_span() -> None:
    facts = _done()
    spans = _all_spans(facts)

    assert {s.context.trace_id for s in spans} == {chunk_trace_id("ch_1")}
    assert len({(s.context.trace_id, s.context.span_id) for s in spans}) == len(spans)
    chunk = _named(tuple(spans), "chunk")
    assert chunk.parent_span_id is None
    assert chunk.context.span_id == chunk_span_id("ch_1")
    roots = [s for s in spans if s.name.startswith("step ")]
    assert len(roots) == 4
    assert {r.parent_span_id for r in roots} == {chunk.context.span_id}


def test_step_roots_keep_their_links_and_reasons() -> None:
    facts = _done()
    steps = identify_steps(facts)
    roots = [assemble_step(facts, s)[0] for s in steps]

    assert [r.links[0].attributes[attr.LINK_REASON] for r in roots[1:]] == ["next", "bounce", "next"]
    assert [r.links[0].context for r in roots[1:]] == [step_root(s.key) for s in steps[:-1]]
    assert not roots[0].links


def test_the_chunk_span_runs_from_ingest_to_done_with_the_chunks_totals() -> None:
    chunk, backlog = assemble_chunk(_done())

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


def test_the_chunk_span_is_derived_not_random() -> None:
    first, second = assemble_chunk(_done()), assemble_chunk(_done())

    assert first == second
    assert first[1].context == chunk_context("ch_1", ChunkRole.BACKLOG, fx.at(0))


def test_a_stopped_chunk_keeps_its_outcome_and_a_later_completion_is_a_marker() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        chunk_stopped=(ChunkStoppedRecord(fx.at(40)),),
        chunk_completed=(ChunkCompletedRecord(fx.at(90)),),
        **fx.runner_epoch(1, 10),
    )

    chunk = assemble_chunk(facts)[0]
    (marker,) = assemble_completion(facts)

    assert chunk.end == fx.at(40)
    assert chunk.attributes[attr.CHUNK_OUTCOME] == "stopped"
    assert marker.name == "chunk completed"
    assert (marker.start, marker.end) == (fx.at(90), fx.at(90))
    assert marker.parent_span_id == chunk.context.span_id
    assert marker.attributes[attr.CHUNK_OUTCOME] == "done"
    assert marker.context.trace_id == chunk.context.trace_id


def test_a_completion_that_ties_the_stop_decides_the_chunk_and_leaves_no_marker() -> None:
    facts = fx.make_facts(
        chunk_stopped=(ChunkStoppedRecord(fx.at(40)),), chunk_completed=(ChunkCompletedRecord(fx.at(40)),)
    )

    end = chunk_end(facts)

    assert end is not None
    assert end.outcome is ChunkOutcome.DONE
    with pytest.raises(ValueError, match="not hand-completed"):
        assemble_completion(facts)


def test_an_unfinished_chunk_is_refused() -> None:
    facts = fx.make_facts(promotions=(PromotionRecord(fx.at(5)),))

    assert chunk_end(facts) is None
    with pytest.raises(ValueError, match="unfinished"):
        assemble_chunk(facts)


def test_an_escalation_wait_covers_the_gap_to_the_requeue() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        routes_created=(RouteCreatedRecord(fx.at(6)), RouteCreatedRecord(fx.at(300))),
        escalations=(EscalationRecord(1, fx.at(40)),),
        requeues=(RequeueRecord(fx.at(300)),),
        transitions=(TransitionRecord(2, fx.at(400), "g1", RESERVED_TERMINAL),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 310)),
    )

    spans = assemble_chunk(facts)
    wait = _named(spans, "escalation wait")
    second = assemble_step(facts, identify_steps(facts)[1])

    assert (wait.start, wait.end) == (fx.at(40), fx.at(300))
    assert wait.parent_span_id == spans[0].context.span_id
    assert _named(second, "queue wait").start == fx.at(300)


def test_an_escalation_still_open_at_the_stop_ends_there() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        escalations=(EscalationRecord(1, fx.at(40)),),
        chunk_stopped=(ChunkStoppedRecord(fx.at(70)),),
        **fx.runner_epoch(1, 10),
    )

    wait = _named(assemble_chunk(facts), "escalation wait")

    assert (wait.start, wait.end) == (fx.at(40), fx.at(70))


def test_a_pause_while_unclaimed_is_a_wait_and_a_pause_inside_a_step_is_not() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        pauses=(
            PauseRecord("p1", True, fx.at(7)),
            PauseRecord("p2", False, fx.at(9)),
            PauseRecord("p3", True, fx.at(20)),
            PauseRecord("p4", False, fx.at(25)),
        ),
        transitions=(TransitionRecord(1, fx.at(60), "g1", RESERVED_TERMINAL),),
        **fx.runner_epoch(1, 10),
    )

    waits = [s for s in assemble_chunk(facts) if s.name == "pause wait"]

    assert [(w.start, w.end) for w in waits] == [(fx.at(7), fx.at(9))]
    assert waits[0].context == chunk_context("ch_1", ChunkRole.PAUSE, fx.at(7))


def test_a_pause_a_restart_closes_the_step_under_is_a_wait_from_the_close_to_the_resume() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        pauses=(PauseRecord("p1", True, fx.at(20)), PauseRecord("p2", False, fx.at(50))),
        restarts=(RestartRecord(2, fx.at(30), "g1", "g1-build"),),
        transitions=(TransitionRecord(3, fx.at(80), "g1", RESERVED_TERMINAL),),
        **fx.merge(
            fx.runner_epoch(1, 10), {"epoch_owners": (EpochOwnerRecord(2, None, fx.at(30)),)}, fx.runner_epoch(3, 55)
        ),
    )

    waits = [s for s in assemble_chunk(facts) if s.name == "pause wait"]
    pause_child = [s for s in assemble_step(facts, identify_steps(facts)[0]) if s.name == "pause"]

    assert [(w.start, w.end) for w in waits] == [(fx.at(30), fx.at(50))]
    assert waits[0].context == chunk_context("ch_1", ChunkRole.PAUSE, fx.at(30))
    assert [(p.start, p.end) for p in pause_child] == [(fx.at(20), fx.at(30))]


def test_a_pause_while_a_gate_holds_the_route_is_a_wait_only_after_the_gate_closes() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(DecisionResolutionRecord("d1", fx.at(40), choice="approve"),),
        pauses=(PauseRecord("p1", True, fx.at(30)), PauseRecord("p2", False, fx.at(520))),
        transitions=(
            fx.to("g1", "build", 500, 2, decision_id="d1", choice_name="approve"),
            TransitionRecord(2, fx.at(600), "g1", RESERVED_TERMINAL),
        ),
        **fx.runner_epoch(1, 10),
    )

    waits = [s for s in assemble_chunk(facts) if s.name == "pause wait"]

    assert [(w.start, w.end) for w in waits] == [(fx.at(500), fx.at(520))]
    assert waits[0].context == chunk_context("ch_1", ChunkRole.PAUSE, fx.at(500))


def test_a_pause_inside_the_backlog_leaves_the_backlog_wait_alone() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(20)),),
        pauses=(PauseRecord("p1", True, fx.at(5)), PauseRecord("p2", False, fx.at(30))),
        transitions=(TransitionRecord(1, fx.at(80), "g1", RESERVED_TERMINAL),),
        **fx.runner_epoch(1, 35),
    )

    spans = assemble_chunk(facts)
    backlog = _named(spans, "backlog wait")
    waits = [s for s in spans if s.name == "pause wait"]

    assert (backlog.start, backlog.end) == (fx.at(0), fx.at(20))
    assert [(w.start, w.end) for w in waits] == [(fx.at(20), fx.at(30))]


def test_a_pause_inside_an_escalation_wait_leaves_it_alone() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        pauses=(PauseRecord("p1", True, fx.at(50)), PauseRecord("p2", False, fx.at(100))),
        escalations=(EscalationRecord(1, fx.at(40)),),
        requeues=(RequeueRecord(fx.at(200)),),
        transitions=(TransitionRecord(2, fx.at(400), "g1", RESERVED_TERMINAL),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 210)),
    )

    spans = assemble_chunk(facts)

    assert (_named(spans, "escalation wait").start, _named(spans, "escalation wait").end) == (fx.at(40), fx.at(200))
    assert not [s for s in spans if s.name == "pause wait"]


def test_a_chunk_never_promoted_rests_in_the_backlog_until_it_ends() -> None:
    facts = fx.make_facts(chunk_stopped=(ChunkStoppedRecord(fx.at(40)),))

    chunk, backlog = assemble_chunk(facts)

    assert (backlog.start, backlog.end) == (fx.at(0), fx.at(40))
    assert chunk.attributes[attr.CHUNK_BACKLOG_MS] == 40000
    assert chunk.attributes[attr.CHUNK_ACTIVE_MS] == 0


def test_instants_in_span_ids_are_utc_microseconds() -> None:
    assert instant_text(datetime(2026, 1, 1, 0, 0, 5, 250000, tzinfo=fx.T0.tzinfo)) == "2026-01-01T00:00:05.250000Z"


def _window(facts: StepFacts, since: CursorKey, limit: int = 100) -> Any:
    return select_window([facts], since, fx.at(10_000), None, limit)


def test_a_sweep_tells_each_chunk_span_exactly_once() -> None:
    facts = _done()
    first = _window(facts, CursorKey.opening(fx.at(0)))

    assert [type(i) for i in first.items].count(ClosedStep) == 4
    assert len(first.finished_chunks()) == 1
    again = _window(facts, first.position)
    assert again.items == ()


def test_a_chunk_span_is_told_before_a_later_completion_marker_and_neither_is_told_twice() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(5)),),
        chunk_stopped=(ChunkStoppedRecord(fx.at(40)),),
        chunk_completed=(ChunkCompletedRecord(fx.at(90)),),
        **fx.runner_epoch(1, 10),
    )

    told = _window(facts, CursorKey.opening(fx.at(0)), limit=100)
    kinds = [(i.completion if isinstance(i, FinishedChunk) else None) for i in told.items]

    assert kinds.count(False) == 1
    assert kinds.count(True) == 1
    assert _window(facts, told.position).items == ()
    # A pass that stopped after the chunk span, before the completion, tells only the marker afterwards.
    chunk_key = CursorKey.chunk_finished(fx.at(40), "ch_1")
    later = _window(facts, chunk_key)
    assert [i.completion for i in later.finished_chunks()] == [True]


def test_a_replay_and_the_sweep_tell_the_same_ids() -> None:
    facts = _done()
    swept = assemble_window(_window(facts, CursorKey.opening(fx.at(0))))
    replayed = assemble_window(_window(facts, CursorKey.opening(fx.at(100)), limit=100))

    chunk_ids = {s.context.span_id for s in swept if s.name == "chunk"}
    assert chunk_ids == {s.context.span_id for s in replayed if s.name == "chunk"} == {chunk_span_id("ch_1")}


def test_a_chunk_without_an_ingest_instant_is_not_a_finished_item() -> None:
    facts = _done(minted_at=None)

    assert _window(facts, CursorKey.opening(fx.at(0))).finished_chunks() == ()
