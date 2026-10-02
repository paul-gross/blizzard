"""Runner lease assembly (unit tier) — one closed lease's facts in, its finished spans out."""

from __future__ import annotations

import dataclasses
from dataclasses import replace

import pytest

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import RunnerSpanRole, SpanRole, StepKey, span_id, trace_id
from blizzard.foundation.trace_spans import SpanKind, SpanRecord, SpanStatus
from blizzard.runner.domain.leases import closure
from blizzard.runner.domain.tracing import attributes as attr
from blizzard.runner.domain.tracing import facts as facts_module
from blizzard.runner.domain.tracing.assembly import assemble_lease
from blizzard.runner.domain.tracing.facts import (
    CheckResultRow,
    ChecksRanRow,
    ContextSampleRow,
    NudgeRow,
    OverloadRow,
    ParkResumeRow,
    ParkRow,
    PauseParkRow,
    PauseResumeRow,
    SessionEndRow,
    TakeoverEndRow,
    TakeoverRow,
)
from tests import runner_trace_fixtures as fx
from tests.trace_contract_support import dictionary

pytestmark = pytest.mark.unit

_KEY = StepKey.attempt(fx.CHUNK_ID, fx.EPOCH)
_MEASURES = {
    shared.GEN_AI_INPUT_TOKENS,
    shared.GEN_AI_OUTPUT_TOKENS,
    shared.GEN_AI_CACHE_READ_TOKENS,
    shared.GEN_AI_CACHE_CREATE_TOKENS,
    shared.INVOCATION_INPUT_TOKENS,
    shared.INVOCATION_OUTPUT_TOKENS,
    shared.INVOCATION_CACHE_READ_TOKENS,
    shared.INVOCATION_CACHE_CREATE_TOKENS,
    shared.INVOCATION_COST_USD,
    shared.INVOCATION_COST_ESTIMATED,
}


def _role(spans: tuple[SpanRecord, ...], role: RunnerSpanRole, discriminator: str = "") -> SpanRecord:
    wanted = span_id(_KEY, role, discriminator)
    return next(s for s in spans if s.context.span_id == wanted)


def _worker(spans: tuple[SpanRecord, ...]) -> SpanRecord:
    return _role(spans, RunnerSpanRole.WORKER, fx.LEASE_ID)


def _invocation(spans: tuple[SpanRecord, ...], generation: int, kind: str) -> SpanRecord:
    return _role(spans, RunnerSpanRole.INVOCATION, f"{generation}/{kind}")


def _step_vector() -> dict[str, str]:
    return next(
        v
        for v in dictionary()["ids"]["vectors"]
        if v["key"] == "ch_1/1" and v["role"] == "step" and not v["discriminator"]
    )


# --- identity -----------------------------------------------------------------------------------------------------


def test_worker_parents_into_the_hubs_step_root_by_the_published_vector() -> None:
    spans = assemble_lease(fx.make_facts())
    vector = _step_vector()
    worker = _worker(spans)
    assert worker.parent_span_id == int(vector["span_id"], 16) == span_id(_KEY, SpanRole.STEP)
    assert {s.context.trace_id for s in spans} == {int(vector["trace_id"], 16)} == {trace_id(_KEY)}


def test_runner_worker_span_id_vector() -> None:
    # printf 'blizzard-span/v1/ch_1/1/runner/worker/lease_1' | sha256sum | cut -c1-16
    assert _worker(assemble_lease(fx.make_facts())).context.span_id == int("b122cf84fa5df19f", 16)


def test_runner_invocation_span_id_vector() -> None:
    # printf 'blizzard-span/v1/ch_1/1/runner/invocation/1/spawn' | sha256sum | cut -c1-16
    assert _invocation(assemble_lease(fx.make_facts()), 1, "spawn").context.span_id == int("72881c7fb0ee66ec", 16)


def test_every_child_parents_into_the_worker() -> None:
    spans = assemble_lease(fx.busy_facts())
    worker = spans[0]
    assert worker is _worker(spans)
    assert all(s.parent_span_id == worker.context.span_id for s in spans[1:])
    assert {s.name for s in spans[1:]} == {
        "invoke_agent builder",
        "parked on ask",
        "parked on pause",
        "provider overload backoff",
        "takeover",
    }


# --- a spawn that transitions --------------------------------------------------------------------------------------


def test_a_spawn_that_transitions() -> None:
    spans = assemble_lease(fx.make_facts(usage=(fx.usage(1, 1, "spawn", 50),)))
    assert len(spans) == 2
    worker, invocation = spans
    assert worker.name == "worker build"
    assert (worker.start, worker.end) == (fx.at(0), fx.at(100))
    assert worker.kind is SpanKind.INTERNAL and worker.status is SpanStatus.UNSET
    a = worker.attributes
    assert a[shared.CHUNK_ID] == fx.CHUNK_ID
    assert a[shared.CHUNK_WORK_REFS] == ("blizzard#745",)
    assert (a[shared.GRAPH_NAME], a[shared.GRAPH_ID]) == ("flow", "g1")
    assert (a[shared.NODE_NAME], a[shared.NODE_ID]) == ("build", "g1-build")
    assert a[shared.NODE_EXECUTOR] == "runner"
    assert a[shared.STEP_EPOCH] == fx.EPOCH
    assert a[attr.RUNNER_ID] == "r-1"
    assert a[attr.LEASE_ID] == fx.LEASE_ID
    assert a[attr.LEASE_CLOSE_REASON] == "transitioned"
    assert a[attr.SESSION_NAME] == "builder"
    assert (a[shared.HARNESS_ID], a[shared.HARNESS_VERSION]) == ("claude-code", "2.1")
    assert (a[attr.MODEL_RESOLVED], a[attr.EFFORT_RESOLVED]) == ("opus", "high")
    assert shared.STEP_VISIT not in a

    assert invocation.name == "invoke_agent builder"
    i = invocation.attributes
    assert i[shared.INVOCATION_KIND] == "spawn"
    assert i[attr.INVOCATION_NUDGE] is False
    assert i[attr.INVOCATION_GENERATION] == 1
    assert i[attr.GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert i[attr.GEN_AI_AGENT_NAME] == "builder"
    assert i[attr.GEN_AI_CONVERSATION_ID] == "sess-1"
    assert i[attr.GEN_AI_REQUEST_MODEL] == "opus"
    assert i[shared.GEN_AI_RESPONSE_MODEL] == "claude-opus"
    assert i[attr.LEASE_ID] == fx.LEASE_ID and i[attr.SESSION_NAME] == "builder"
    assert [e.name for e in invocation.events] == ["session identified"]
    assert invocation.events[0].time == fx.at(2)


def test_unknown_context_names_are_omitted_not_guessed() -> None:
    facts = fx.with_context(
        fx.make_facts(), graph_name=None, work_refs=(), session_name=None, resolved_model=None, resolved_effort=None
    )
    worker, invocation = assemble_lease(facts)
    for key in (shared.GRAPH_NAME, shared.CHUNK_WORK_REFS, attr.SESSION_NAME, attr.MODEL_RESOLVED):
        assert key not in worker.attributes
    assert attr.EFFORT_RESOLVED not in worker.attributes
    assert invocation.name == "invoke_agent"
    assert attr.GEN_AI_AGENT_NAME not in invocation.attributes
    assert attr.GEN_AI_REQUEST_MODEL not in invocation.attributes


def test_a_lease_whose_spawn_was_never_identified_tells_nothing() -> None:
    assert assemble_lease(fx.make_facts(spawns=(fx.spawn(1, 1, identified=False),))) == ()
    assert assemble_lease(fx.make_facts(spawns=(), boundaries=())) == ()


def test_worker_never_ends_before_it_started() -> None:
    worker = assemble_lease(replace(fx.make_facts(), closure=replace(fx.make_facts().closure, closed_at=fx.at(-5))))[0]
    assert worker.end == worker.start == fx.at(0)


# --- close reasons ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", sorted(closure.PUBLISHED_REASONS))
def test_each_published_close_reason_is_carried_verbatim(reason: str) -> None:
    assert _worker(assemble_lease(fx.make_facts(reason=reason))).attributes[attr.LEASE_CLOSE_REASON] == reason


@pytest.mark.parametrize("reason", sorted(closure.MINT_REASONS))
def test_mint_reasons_read_escalated(reason: str) -> None:
    assert _worker(assemble_lease(fx.make_facts(reason=reason))).attributes[attr.LEASE_CLOSE_REASON] == "escalated"


def test_a_chunk_the_hub_stopped_closes_released_and_open_children_end_at_the_close() -> None:
    facts = fx.make_facts(
        reason=closure.RELEASED,
        closed=60,
        parks=(ParkRow(1, "q1", fx.at(20)),),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 60),),
    )
    spans = assemble_lease(facts)
    worker = _worker(spans)
    assert worker.attributes[attr.LEASE_CLOSE_REASON] == "released"
    assert worker.end == fx.at(60)
    assert _role(spans, RunnerSpanRole.ASK_PARK, "q1").end == fx.at(60)


# --- invocation ends -------------------------------------------------------------------------------------------------


def test_end_source_session_end() -> None:
    facts = fx.make_facts(
        session_ends=(SessionEndRow(1, fx.at(40)), SessionEndRow(2, fx.at(45))),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 1, "judge", 50, 100)),
    )
    invocation = _invocation(assemble_lease(facts), 1, "spawn")
    assert invocation.end == fx.at(40)
    assert invocation.attributes[attr.INVOCATION_END_SOURCE] == "session_end"
    assert [e.time for e in invocation.events if e.name == "session end"] == [fx.at(40)]


def test_a_session_end_before_the_invocation_opened_is_not_its_end() -> None:
    facts = fx.make_facts(
        spawns=(fx.spawn(1, 1), fx.spawn(2, 30)),
        session_ends=(SessionEndRow(1, fx.at(20)),),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 30, 100)),
    )
    second = _invocation(assemble_lease(facts), 2, "resume")
    assert second.attributes[attr.INVOCATION_END_SOURCE] == "lease_close"
    assert second.end == fx.at(100)
    assert not [e for e in second.events if e.name == "session end"]


def test_end_source_next_invocation() -> None:
    facts = fx.make_facts(boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 1, "judge", 50, 100)))
    spans = assemble_lease(facts)
    first = _invocation(spans, 1, "spawn")
    assert first.end == fx.at(50)
    assert first.attributes[attr.INVOCATION_END_SOURCE] == "next_invocation"
    assert _invocation(spans, 1, "judge").attributes[attr.INVOCATION_END_SOURCE] == "lease_close"


def test_end_source_lease_close() -> None:
    invocation = _invocation(assemble_lease(fx.make_facts(boundaries=(fx.boundary(1, 1, "spawn", 1, 90),))), 1, "spawn")
    assert invocation.end == fx.at(90)
    assert invocation.attributes[attr.INVOCATION_END_SOURCE] == "lease_close"


def test_an_unclosed_boundary_ends_at_the_lease_close() -> None:
    invocation = _invocation(assemble_lease(fx.make_facts(boundaries=(fx.boundary(1, 1, "spawn", 1),))), 1, "spawn")
    assert invocation.end == fx.at(100)
    assert invocation.attributes[attr.INVOCATION_END_SOURCE] == "lease_close"


def test_a_session_end_tie_with_the_next_invocation_reads_session_end() -> None:
    facts = fx.make_facts(
        session_ends=(SessionEndRow(1, fx.at(50)),),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 1, "judge", 50, 100)),
    )
    assert _invocation(assemble_lease(facts), 1, "spawn").attributes[attr.INVOCATION_END_SOURCE] == "session_end"


def test_a_child_ending_after_the_lease_is_clamped_to_its_close() -> None:
    facts = fx.make_facts(
        boundaries=(fx.boundary(1, 1, "spawn", 1, 150),), takeovers=(TakeoverRow("tko_1", fx.at(120)),)
    )
    spans = assemble_lease(facts)
    assert _invocation(spans, 1, "spawn").end == fx.at(100)
    takeover = _role(spans, RunnerSpanRole.TAKEOVER, "tko_1")
    assert (takeover.start, takeover.end) == (fx.at(100), fx.at(100))


def test_a_replay_after_opened_at_advanced_keeps_ids_and_starts_later() -> None:
    first = assemble_lease(fx.make_facts())
    later = assemble_lease(fx.make_facts(boundaries=(fx.boundary(1, 1, "spawn", 7, 100),)))
    assert [s.context for s in first] == [s.context for s in later]
    assert _invocation(first, 1, "spawn").start == fx.at(1)
    assert _invocation(later, 1, "spawn").start == fx.at(7)


# --- joining the record ----------------------------------------------------------------------------------------------


def test_spawn_rows_join_generations_by_order_across_a_resume_and_a_nudge() -> None:
    spawns = (
        fx.spawn(9, 40, session="sess-c"),
        fx.spawn(3, 1, session="sess-a"),
        replace(fx.spawn(5, 20, session="sess-b"), harness_id="opencode", harness_version="0.9"),
    )
    facts = fx.make_facts(
        spawns=spawns,
        boundaries=(
            fx.boundary(1, 1, "spawn", 1, 100),
            fx.boundary(2, 2, "resume", 20, 100),
            fx.boundary(3, 3, "nudge", 40, 100),
        ),
    )
    spans = assemble_lease(facts)
    resume, nudge = _invocation(spans, 2, "resume"), _invocation(spans, 3, "nudge")
    assert _invocation(spans, 1, "spawn").attributes[attr.GEN_AI_CONVERSATION_ID] == "sess-a"
    assert resume.attributes[attr.GEN_AI_CONVERSATION_ID] == "sess-b"
    assert (resume.attributes[shared.HARNESS_ID], resume.attributes[shared.HARNESS_VERSION]) == ("opencode", "0.9")
    assert nudge.attributes[attr.GEN_AI_CONVERSATION_ID] == "sess-c"
    assert [e.time for e in resume.events if e.name == "session identified"] == [fx.at(21)]
    assert _worker(spans).attributes[shared.HARNESS_ID] == "claude-code"


def test_a_generation_with_no_spawn_row_carries_no_session() -> None:
    facts = fx.make_facts(boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 50, 100)))
    resume = _invocation(assemble_lease(facts), 2, "resume")
    assert attr.GEN_AI_CONVERSATION_ID not in resume.attributes
    assert shared.HARNESS_ID not in resume.attributes
    assert not resume.events


def test_a_nudge_after_a_quiet_worker() -> None:
    facts = fx.make_facts(
        spawns=(fx.spawn(1, 1), fx.spawn(2, 60)),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "nudge", 60, 100)),
        nudges=(NudgeRow(1, fx.EPOCH, fx.at(59)),),
        usage=(fx.usage(1, 2, "resume", 90, input_tokens=7),),
    )
    spans = assemble_lease(facts)
    nudge = _invocation(spans, 2, "nudge")
    assert nudge.attributes[shared.INVOCATION_KIND] == "resume"
    assert nudge.attributes[attr.INVOCATION_NUDGE] is True
    assert nudge.attributes[shared.INVOCATION_INPUT_TOKENS] == 7
    assert [(e.name, e.time) for e in _worker(spans).events] == [("nudge", fx.at(59))]


def test_a_judgement_takes_its_own_usage_not_the_workers() -> None:
    facts = fx.make_facts(
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 1, "judge", 50, 100)),
        usage=(
            fx.usage(1, 1, "spawn", 49, input_tokens=100),
            fx.usage(2, 1, "judge", 60, input_tokens=3, model="haiku", harness_id="opencode", harness_version="0.9"),
        ),
    )
    spans = assemble_lease(facts)
    worker_inv, judge = _invocation(spans, 1, "spawn"), _invocation(spans, 1, "judge")
    assert worker_inv.attributes[shared.INVOCATION_INPUT_TOKENS] == 100
    assert judge.attributes[shared.INVOCATION_INPUT_TOKENS] == 3
    assert judge.attributes[shared.INVOCATION_KIND] == "judge"
    assert judge.attributes[shared.GEN_AI_RESPONSE_MODEL] == "haiku"
    assert (judge.attributes[shared.HARNESS_ID], judge.attributes[shared.HARNESS_VERSION]) == ("opencode", "0.9")
    assert attr.GEN_AI_CONVERSATION_ID not in judge.attributes
    assert not [e for e in judge.events if e.name == "session identified"]


def test_a_judge_with_no_harnessed_usage_carries_no_harness() -> None:
    facts = fx.make_facts(
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 1, "judge", 50, 100)),
        usage=(fx.usage(2, 1, "judge", 60),),
    )
    assert shared.HARNESS_ID not in _invocation(assemble_lease(facts), 1, "judge").attributes


def test_usage_matches_by_generation_and_side_including_the_late_shutdown_drain() -> None:
    facts = fx.make_facts(
        spawns=(fx.spawn(1, 1), fx.spawn(2, 50)),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 50, 100)),
        usage=(
            fx.usage(1, 1, "spawn", 40, input_tokens=1),
            fx.usage(2, 2, "resume", 80, input_tokens=10, model="opus-old"),
            fx.usage(3, 2, "spawn", 130, input_tokens=100, model="opus-new"),
        ),
    )
    spans = assemble_lease(facts)
    assert _invocation(spans, 1, "spawn").attributes[shared.INVOCATION_INPUT_TOKENS] == 1
    second = _invocation(spans, 2, "resume")
    assert second.attributes[shared.INVOCATION_INPUT_TOKENS] == 110
    assert second.attributes[shared.GEN_AI_RESPONSE_MODEL] == "opus-new"


# --- measures --------------------------------------------------------------------------------------------------------


def test_measures_sit_only_on_invocation_spans_with_cached_input_in_genai_input() -> None:
    facts = fx.make_facts(
        usage=(
            fx.usage(1, 1, "spawn", 40, input_tokens=100, cache_read_tokens=1000, cache_create_tokens=50),
            fx.usage(2, 1, "spawn", 41, input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_create_tokens=4),
        ),
        parks=(ParkRow(1, "q1", fx.at(20)),),
        overloads=(OverloadRow(1, 1, 1, fx.at(30)),),
    )
    spans = assemble_lease(facts)
    invocation = _invocation(spans, 1, "spawn")
    for span in spans:
        if span is not invocation:
            assert not _MEASURES & set(span.attributes), span.name
    a = invocation.attributes
    assert a[shared.GEN_AI_INPUT_TOKENS] == 101 + 1003 + 54
    assert a[shared.INVOCATION_INPUT_TOKENS] == 101
    assert a[shared.GEN_AI_OUTPUT_TOKENS] == a[shared.INVOCATION_OUTPUT_TOKENS] == 12
    assert a[shared.GEN_AI_CACHE_READ_TOKENS] == a[shared.INVOCATION_CACHE_READ_TOKENS] == 1003
    assert a[shared.GEN_AI_CACHE_CREATE_TOKENS] == a[shared.INVOCATION_CACHE_CREATE_TOKENS] == 54


def test_an_invocation_with_no_usage_carries_no_measures() -> None:
    assert not _MEASURES & set(_invocation(assemble_lease(fx.make_facts()), 1, "spawn").attributes)
    assert shared.GEN_AI_RESPONSE_MODEL not in _invocation(assemble_lease(fx.make_facts()), 1, "spawn").attributes


def test_cost_folds_billed_and_estimate() -> None:
    facts = fx.make_facts(
        usage=(
            fx.usage(1, 1, "spawn", 40, cost_usd=0.5),
            fx.usage(2, 1, "spawn", 41, cost_usd=None, estimated_cost_usd=0.25),
        )
    )
    a = _invocation(assemble_lease(facts), 1, "spawn").attributes
    assert a[shared.INVOCATION_COST_USD] == pytest.approx(0.75)
    assert a[shared.INVOCATION_COST_ESTIMATED] is True


def test_billed_cost_alone_is_not_estimated() -> None:
    a = _invocation(assemble_lease(fx.make_facts(usage=(fx.usage(1, 1, "spawn", 40),))), 1, "spawn").attributes
    assert a[shared.INVOCATION_COST_USD] == pytest.approx(0.5)
    assert a[shared.INVOCATION_COST_ESTIMATED] is False


def test_an_unbilled_invocation_still_carries_its_estimate() -> None:
    facts = fx.make_facts(usage=(fx.usage(1, 1, "spawn", 40, cost_usd=None, estimated_cost_usd=0.2),))
    a = _invocation(assemble_lease(facts), 1, "spawn").attributes
    assert a[shared.INVOCATION_COST_USD] == pytest.approx(0.2)
    assert a[shared.INVOCATION_COST_ESTIMATED] is True


def test_cost_is_omitted_when_no_row_has_a_figure() -> None:
    a = _invocation(
        assemble_lease(fx.make_facts(usage=(fx.usage(1, 1, "spawn", 40, cost_usd=None),))), 1, "spawn"
    ).attributes
    assert shared.INVOCATION_COST_USD not in a
    assert shared.INVOCATION_COST_ESTIMATED not in a
    assert a[shared.INVOCATION_INPUT_TOKENS] == 100


# --- waits ------------------------------------------------------------------------------------------------------------


def test_a_spawn_a_park_on_an_ask_and_a_resume_after_the_answer() -> None:
    facts = fx.make_facts(
        spawns=(fx.spawn(1, 1), fx.spawn(2, 41)),
        session_ends=(SessionEndRow(1, fx.at(19)),),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 41, 100)),
        parks=(ParkRow(1, "q1", fx.at(20)),),
        park_resumes=(ParkResumeRow(1, "q0", fx.at(30)), ParkResumeRow(2, "q1", fx.at(40))),
    )
    spans = assemble_lease(facts)
    park = _role(spans, RunnerSpanRole.ASK_PARK, "q1")
    assert park.name == "parked on ask"
    assert (park.start, park.end) == (fx.at(20), fx.at(40))
    assert park.attributes[attr.LEASE_ID] == fx.LEASE_ID
    assert _invocation(spans, 1, "spawn").end == fx.at(19)
    assert _invocation(spans, 2, "resume").start == fx.at(41)
    assert [s.start for s in spans[1:]] == sorted(s.start for s in spans[1:])


def test_a_resume_recorded_before_the_park_does_not_end_it() -> None:
    facts = fx.make_facts(parks=(ParkRow(1, "q1", fx.at(20)),), park_resumes=(ParkResumeRow(1, "q1", fx.at(10)),))
    assert _role(assemble_lease(facts), RunnerSpanRole.ASK_PARK, "q1").end == fx.at(100)


def test_a_pause_park_ends_at_the_next_resume_or_the_close() -> None:
    facts = fx.make_facts(
        pause_parks=(PauseParkRow(4, fx.at(20)), PauseParkRow(7, fx.at(60))),
        pause_resumes=(PauseResumeRow(1, fx.at(10)), PauseResumeRow(2, fx.at(45)), PauseResumeRow(3, fx.at(50))),
    )
    spans = assemble_lease(facts)
    first, second = _role(spans, RunnerSpanRole.PAUSE_PARK, "4"), _role(spans, RunnerSpanRole.PAUSE_PARK, "7")
    assert first.name == "parked on pause"
    assert (first.start, first.end) == (fx.at(20), fx.at(45))
    assert (second.start, second.end) == (fx.at(60), fx.at(100))


def test_an_overload_backoff_followed_by_a_resume() -> None:
    facts = fx.make_facts(
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 25, 100)),
        overloads=(
            OverloadRow(1, 1, 2, fx.at(10), resume_after=fx.at(30)),
            OverloadRow(2, 2, 1, fx.at(40), resume_after=fx.at(55)),
            OverloadRow(3, 2, 3, fx.at(70)),
        ),
    )
    spans = assemble_lease(facts)
    before_resume = _role(spans, RunnerSpanRole.OVERLOAD, "1")
    assert before_resume.name == "provider overload backoff"
    assert (before_resume.start, before_resume.end) == (fx.at(10), fx.at(25))
    assert before_resume.attributes[attr.OVERLOAD_STREAK] == 2
    assert _role(spans, RunnerSpanRole.OVERLOAD, "2").end == fx.at(55)
    assert _role(spans, RunnerSpanRole.OVERLOAD, "3").end == fx.at(100)


def test_a_takeover_ends_at_its_end_or_the_close() -> None:
    facts = fx.make_facts(
        takeovers=(TakeoverRow("tko_1", fx.at(20)), TakeoverRow("tko_2", fx.at(50))),
        takeover_ends=(TakeoverEndRow(1, "tko_1", fx.at(35)),),
    )
    spans = assemble_lease(facts)
    ended = _role(spans, RunnerSpanRole.TAKEOVER, "tko_1")
    assert ended.name == "takeover"
    assert (ended.start, ended.end) == (fx.at(20), fx.at(35))
    assert _role(spans, RunnerSpanRole.TAKEOVER, "tko_2").end == fx.at(100)


# --- events ----------------------------------------------------------------------------------------------------------


def test_context_samples_land_on_the_invocation_whose_window_holds_them() -> None:
    facts = fx.make_facts(
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 1, "judge", 50, 100)),
        context_samples=(
            ContextSampleRow(1, fx.at(10), 5000),
            ContextSampleRow(2, fx.at(60), None),
            ContextSampleRow(3, fx.at(0), 1),
        ),
    )
    spans = assemble_lease(facts)
    worker_samples = [e for e in _invocation(spans, 1, "spawn").events if e.name == "context sample"]
    assert [(e.time, dict(e.attributes)) for e in worker_samples] == [(fx.at(10), {attr.CONTEXT_TOKENS: 5000})]
    judge_samples = [e for e in _invocation(spans, 1, "judge").events if e.name == "context sample"]
    assert [(e.time, dict(e.attributes)) for e in judge_samples] == [(fx.at(60), {})]


def test_checks_that_all_pass() -> None:
    facts = fx.make_facts(
        check_results=(CheckResultRow(1, fx.EPOCH, True), CheckResultRow(2, fx.EPOCH, True)),
        checks_ran=(ChecksRanRow(1, fx.EPOCH, fx.at(80)),),
    )
    events = _worker(assemble_lease(facts)).events
    assert [(e.name, dict(e.attributes)) for e in events] == [
        ("check", {attr.CHECK_INDEX: 1, attr.CHECK_PASSED: True}),
        ("check", {attr.CHECK_INDEX: 2, attr.CHECK_PASSED: True}),
        ("checks ran", {attr.CHECKS_PASSED: True, attr.CHECKS_COUNT: 2}),
    ]
    assert {e.time for e in events} == {fx.at(80)}


def test_checks_with_one_failing_are_indexed_by_row_id() -> None:
    facts = fx.make_facts(
        check_results=(
            CheckResultRow(12, fx.EPOCH, True),
            CheckResultRow(10, fx.EPOCH, True),
            CheckResultRow(11, fx.EPOCH, False),
            CheckResultRow(3, fx.EPOCH + 1, False),
        ),
        checks_ran=(ChecksRanRow(1, fx.EPOCH, fx.at(80)),),
    )
    events = _worker(assemble_lease(facts)).events
    assert [dict(e.attributes) for e in events] == [
        {attr.CHECK_INDEX: 1, attr.CHECK_PASSED: True},
        {attr.CHECK_INDEX: 2, attr.CHECK_PASSED: False},
        {attr.CHECK_INDEX: 3, attr.CHECK_PASSED: True},
        {attr.CHECKS_PASSED: False, attr.CHECKS_COUNT: 3},
    ]


def test_check_results_without_a_checks_ran_marker_tell_nothing() -> None:
    facts = fx.make_facts(check_results=(CheckResultRow(1, fx.EPOCH, True),))
    assert _worker(assemble_lease(facts)).events == ()


def test_worker_events_are_clamped_into_the_lease() -> None:
    facts = fx.make_facts(
        nudges=(NudgeRow(1, fx.EPOCH, fx.at(130)), NudgeRow(2, fx.EPOCH, fx.at(-3))),
        check_results=(CheckResultRow(1, fx.EPOCH, True),),
        checks_ran=(ChecksRanRow(1, fx.EPOCH, fx.at(50)),),
    )
    assert [(e.name, e.time) for e in _worker(assemble_lease(facts)).events] == [
        ("nudge", fx.at(0)),
        ("check", fx.at(50)),
        ("checks ran", fx.at(50)),
        ("nudge", fx.at(100)),
    ]


# --- shape -----------------------------------------------------------------------------------------------------------


def test_every_emitted_key_is_declared_and_every_span_is_internal_and_unset() -> None:
    spans = assemble_lease(fx.busy_facts())
    assert len(spans) == 7
    for span in spans:
        assert span.kind is SpanKind.INTERNAL and span.status is SpanStatus.UNSET
        assert not span.links
        assert set(span.attributes) <= attr.DECLARED_ATTRIBUTES, span.name
        for event in span.events:
            assert set(event.attributes) <= attr.DECLARED_ATTRIBUTES, event.name
        assert span.start <= span.end <= spans[0].end


def test_the_runner_scope_is_its_own() -> None:
    assert attr.INSTRUMENTATION_SCOPE == "blizzard.runner.runner_spans"
    assert attr.INSTRUMENTATION_SCOPE not in attr.DECLARED_ATTRIBUTES


# --- what never leaves -----------------------------------------------------------------------------------------------

_CONTENT_COLUMNS = {
    "question",
    "options",
    "command",
    "output_tail",
    "workdir",
    "output_path",
    "pid",
    "pgid",
    "process_start_time",
    "start_position",
    "payload",
    "interrupted_elicitation_id",
}


def test_no_facts_row_declares_a_content_column() -> None:
    row_types = [
        t
        for t in vars(facts_module).values()
        if isinstance(t, type) and dataclasses.is_dataclass(t) and t.__module__ == facts_module.__name__
    ]
    assert len(row_types) == 19
    for row_type in row_types:
        assert not _CONTENT_COLUMNS & {f.name for f in dataclasses.fields(row_type)}, row_type.__name__


def _load(row_type: type, row: dict[str, object]) -> object:
    """A careless loader: every column of the table row, kept wherever the row type has a field by that name."""
    return row_type(**{f.name: row[f.name] for f in dataclasses.fields(row_type) if f.name in row})


def test_planted_content_never_reaches_a_span() -> None:
    planted = ("What is the secret plan?", "pytest -k secret", "FAILED secret output", "/home/secret/workdir")
    question, command, output, workdir = planted
    facts = replace(
        fx.busy_facts(),
        parks=(
            _load(
                ParkRow,
                {"id": 1, "question_id": "q1", "parked_at": fx.at(20), "question": question, "options": "[]"},
            ),
        ),  # type: ignore[arg-type]
        check_results=(
            _load(
                CheckResultRow,
                {"id": 1, "epoch": fx.EPOCH, "passed": False, "command": command, "output_tail": output},
            ),
        ),  # type: ignore[arg-type]
        takeovers=(
            _load(TakeoverRow, {"takeover_id": "tko_1", "opened_at": fx.at(80), "workdir": workdir, "pid": 4242}),
        ),  # type: ignore[arg-type]
    )
    spans = assemble_lease(facts)
    rendered = repr([(s.name, s.attributes, [(e.name, e.attributes) for e in s.events]) for s in spans])
    for secret in (*planted, "4242"):
        assert secret not in rendered


# --- boundaries at the same instant --------------------------------------------------------------------------------


def test_a_session_end_at_the_instant_an_invocation_opened_belongs_to_the_one_before() -> None:
    facts = fx.make_facts(
        spawns=(fx.spawn(1, 1), fx.spawn(2, 50)),
        session_ends=(SessionEndRow(1, fx.at(50)),),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 50, 100)),
    )
    second = _invocation(assemble_lease(facts), 2, "resume")
    assert second.attributes[attr.INVOCATION_END_SOURCE] == "lease_close"
    assert not [e for e in second.events if e.name == "session end"]


def test_a_context_sample_at_the_instant_an_invocation_opened_is_its_own() -> None:
    facts = fx.make_facts(context_samples=(ContextSampleRow(1, fx.at(1), 10),))
    assert [e.name for e in _invocation(assemble_lease(facts), 1, "spawn").events] == [
        "context sample",
        "session identified",
    ]


def test_a_resume_at_the_instant_of_its_park_ends_it_there() -> None:
    facts = fx.make_facts(
        parks=(ParkRow(1, "q1", fx.at(20)),),
        park_resumes=(ParkResumeRow(1, "q1", fx.at(20)),),
        pause_parks=(PauseParkRow(1, fx.at(30)),),
        pause_resumes=(PauseResumeRow(1, fx.at(30)),),
    )
    spans = assemble_lease(facts)
    assert _role(spans, RunnerSpanRole.ASK_PARK, "q1").end == fx.at(20)
    assert _role(spans, RunnerSpanRole.PAUSE_PARK, "1").end == fx.at(30)


def test_an_invocation_opened_at_the_overloads_instant_does_not_end_it() -> None:
    facts = fx.make_facts(
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 30, 100)),
        overloads=(OverloadRow(1, 1, 1, fx.at(30), resume_after=fx.at(45)),),
    )
    assert _role(assemble_lease(facts), RunnerSpanRole.OVERLOAD, "1").end == fx.at(45)


def test_checks_runs_at_one_instant_tell_in_row_id_order() -> None:
    facts = fx.make_facts(
        check_results=(CheckResultRow(1, fx.EPOCH, True), CheckResultRow(2, fx.EPOCH + 1, False)),
        checks_ran=(ChecksRanRow(2, fx.EPOCH + 1, fx.at(80)), ChecksRanRow(1, fx.EPOCH, fx.at(80))),
    )
    assert [e.attributes.get(attr.CHECKS_PASSED) for e in _worker(assemble_lease(facts)).events] == [
        None,
        True,
        None,
        False,
    ]
