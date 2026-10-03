"""Span assembly (unit tier) — ``StepFacts`` built directly, no store."""

from __future__ import annotations

from dataclasses import replace

import pytest

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey, chunk_span_id
from blizzard.foundation.trace_spans import SpanKind, SpanRecord, SpanStatus
from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.facts import (
    BounceRecord,
    ChunkCompletedRecord,
    ChunkStoppedRecord,
    DecisionRecord,
    DecisionResolutionRecord,
    EpochOwnerRecord,
    EscalationRecord,
    HubExecSlotRecord,
    HubPollRecord,
    LeaseRecord,
    MigrationRecord,
    PauseRecord,
    PrerequisiteMetRecord,
    PromotionRecord,
    QuestionRecord,
    RequeueRecord,
    RestartRecord,
    RouteCreatedRecord,
    RouteReleasedRecord,
    StepFacts,
)
from blizzard.hub.domain.tracing.steps import StepKind, identify_steps
from blizzard.hub.domain.work import UsageTotal
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL-do-not-leak"


def _told(facts: StepFacts) -> dict[str, tuple[SpanRecord, ...]]:
    """Every closed step's spans, keyed by the step key's text."""
    return {s.key.text(): assemble_step(facts, s) for s in identify_steps(facts) if s.close is not None}


def _step(facts: StepFacts, index: int = 0) -> tuple[SpanRecord, ...]:
    step = identify_steps(facts)[index]
    return assemble_step(facts, step)


def _by_name(spans: tuple[SpanRecord, ...]) -> dict[str, SpanRecord]:
    return {s.name: s for s in spans}


def test_runner_step_root() -> None:
    facts = fx.make_facts(
        work_refs=("blizzard#745",),
        usage=(fx.usage(at_seconds=14),),
        transitions=(fx.to("g1", "review", 30, 1, choice_name="pass"),),
        **fx.runner_epoch(1, 10),
    )
    (root,) = _step(facts)
    assert root.name == "step build"
    assert (root.start, root.end) == (fx.at(10), fx.at(30))
    assert root.kind is SpanKind.INTERNAL
    assert root.status is SpanStatus.UNSET
    assert root.parent_span_id == chunk_span_id("ch_1")
    assert root.context == DerivedContext.of(StepKey.attempt("ch_1", 1), SpanRole.STEP)
    a = root.attributes
    assert a[shared.CHUNK_ID] == "ch_1"
    assert a[shared.CHUNK_WORK_REFS] == ("blizzard#745",)
    assert a[shared.NODE_NAME] == "build"
    assert a[shared.NODE_EXECUTOR] == "runner"
    assert a[shared.STEP_VISIT] == 1
    assert a[attr.STEP_OUTCOME] == "transitioned"
    assert a[attr.STEP_CHOICE] == "pass"
    assert a[attr.STEP_TO_NODE_NAME] == "review"
    assert a[attr.RUNNER_ID] == "r-1"
    assert a[shared.HARNESS_ID] == "claude-code"
    assert a[attr.STEP_MODELS] == ("claude-x",)
    assert attr.STEP_PRECEDED_BY not in a
    assert root.links == ()
    assert [e.name for e in root.events] == ["invocation"]


def test_first_claim_has_queue_and_claim_with_pause_excluded_from_the_wait() -> None:
    facts = fx.make_facts(
        promotions=(PromotionRecord(fx.at(2)),),
        pauses=(PauseRecord("p1", True, fx.at(3)), PauseRecord("p2", False, fx.at(6))),
        prerequisites_met=(PrerequisiteMetRecord(fx.at(4)),),
        routes_created=(RouteCreatedRecord(fx.at(20)),),
        transitions=(fx.to("g1", "review", 90, 1),),
        **fx.runner_epoch(1, 25),
    )
    root, queue, claim = _step(facts)
    assert (queue.name, queue.start, queue.end) == ("queue wait", fx.at(6), fx.at(20))
    assert (claim.name, claim.start, claim.end) == ("claim", fx.at(20), fx.at(25))
    assert queue.parent_span_id == root.context.span_id == claim.parent_span_id
    assert root.attributes[attr.WAIT_QUEUE_MS] == 14000
    assert root.attributes[attr.WAIT_CLAIM_MS] == 5000
    assert attr.WAIT_QUEUE_MS not in queue.attributes
    assert queue.attributes[shared.CHUNK_ID] == "ch_1"


def test_a_hub_first_step_has_a_queue_but_no_claim() -> None:
    facts = fx.make_facts(
        routes_created=(RouteCreatedRecord(fx.at(2)),),
        transitions=(fx.to("g1", "gate", 3, 0), fx.to("g1", "review", 50, 1)),
        **fx.runner_epoch(1, 9, runner=None),
    )
    assert [s.name for s in _step(facts)] == ["step gate", "queue wait"]


def test_unused_claim_makes_no_spans_and_marks_the_next_step() -> None:
    facts = fx.make_facts(
        epoch_owners=(EpochOwnerRecord(1, "r-1", fx.at(5)), EpochOwnerRecord(2, "r-1", fx.at(9))),
        lease_facts=(LeaseRecord(2, fx.at(10)),),
        transitions=(fx.to("g1", "review", 30, 2),),
    )
    told = _told(facts)
    assert list(told) == ["ch_1/2"]
    assert told["ch_1/2"][0].attributes[attr.STEP_PRECEDED_BY] == "released-claim"


def test_graph_gate_resolved_has_pickup_and_pickup_ms() -> None:
    facts = fx.make_facts(
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(DecisionResolutionRecord("d1", fx.at(40), choice="approve"),),
        transitions=(fx.to("g1", "build", 500, 2, decision_id="d1", choice_name="approve"),),
        usage=(fx.usage(),),
        **fx.runner_epoch(1, 10),
    )
    root, pickup = _step(facts, 1)
    assert root.name == "gate gate"
    assert (root.start, root.end) == (fx.at(20), fx.at(40))
    assert root.context == DerivedContext.of(StepKey.gate("ch_1", 1, "d1"), SpanRole.GATE)
    assert (pickup.name, pickup.start, pickup.end) == ("decision pickup", fx.at(40), fx.at(500))
    assert root.attributes[attr.WAIT_PICKUP_MS] == 460_000
    assert root.attributes[shared.NODE_EXECUTOR] == "human"
    assert root.attributes[attr.STEP_OUTCOME] == "decided"
    assert root.attributes[attr.STEP_CHOICE] == "approve"
    assert root.attributes[attr.RUNNER_ID] == "r-1"
    assert root.events == ()
    assert root.attributes[attr.STEP_INPUT_TOKENS] == 0


def test_runner_gate_unresolved_ends_at_its_closing_fact() -> None:
    facts = fx.make_facts(
        decisions=(DecisionRecord("d1", "g1-review", 1, fx.at(20), imposed_by_runner_id="r-9"),),
        transitions=(fx.to("g1", "build", 70, 2, decision_id="d1"),),
        **fx.runner_epoch(1, 10),
    )
    (root,) = _step(facts, 1)
    assert (root.start, root.end) == (fx.at(20), fx.at(70))
    assert root.attributes[attr.RUNNER_ID] == "r-9"
    assert root.attributes[attr.WAIT_PICKUP_MS] == 0


@pytest.mark.parametrize(
    ("extra", "outcome", "to_node", "status"),
    [
        ({"transitions": (fx.to("g1", "review", 60, 2, decision_id="d1"),)}, "decided", "review", SpanStatus.UNSET),
        (
            {"migrations": (MigrationRecord(2, fx.at(60), "g1", "g2", decision_id="d1"),)},
            "migrated",
            "graph:flow",
            SpanStatus.UNSET,
        ),
        ({"escalations": (EscalationRecord(2, fx.at(60), decision_id="d1"),)}, "escalated", None, SpanStatus.ERROR),
        (
            {"restarts": (RestartRecord(2, fx.at(60), "g1", "g1-build", decision_id="d1"),)},
            "restarted",
            "build",
            SpanStatus.UNSET,
        ),
    ],
)
def test_gate_closings(
    extra: dict[str, tuple[object, ...]], outcome: str, to_node: str | None, status: SpanStatus
) -> None:
    facts = fx.make_facts(
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(DecisionResolutionRecord("d1", fx.at(40)),),
        **fx.merge(fx.runner_epoch(1, 10), extra),
    )
    root = _step(facts, 1)[0]
    assert root.attributes[attr.STEP_OUTCOME] == outcome
    assert root.attributes.get(attr.STEP_TO_NODE_NAME) == to_node
    assert root.status is status


def test_two_gates_on_one_epoch_each_assemble() -> None:
    facts = fx.make_facts(
        decisions=(
            DecisionRecord("d1", "g1-review", 1, fx.at(20), imposed_by_runner_id="r-1"),
            DecisionRecord("d2", "g1-gate", 1, fx.at(30)),
        ),
        transitions=(fx.to("g1", "build", 80, 2, decision_id="d1"), fx.to("g1", "build", 90, 2, decision_id="d2")),
        **fx.runner_epoch(1, 10),
    )
    told = _told(facts)
    assert {"ch_1/1/gate/d1", "ch_1/1/gate/d2"} <= set(told)
    assert told["ch_1/1/gate/d1"][0].context != told["ch_1/1/gate/d2"][0].context


def test_ask_clamps_clock_skew_and_unanswered_runs_to_step_end() -> None:
    facts = fx.make_facts(
        questions=(
            QuestionRecord("q1", 1, fx.at(20), answered_at=fx.at(15)),
            QuestionRecord("q2", 1, fx.at(25), answered_at=fx.at(28)),
            QuestionRecord("q3", 1, fx.at(26)),
        ),
        transitions=(fx.to("g1", "review", 40, 1),),
        **fx.runner_epoch(1, 10),
    )
    root, *asks = _step(facts)
    skewed, answered, open_ = asks
    assert skewed.start == skewed.end == fx.at(20)
    assert skewed.attributes[attr.CLOCK_SKEW] is True
    assert skewed.attributes[attr.ASK_ANSWERED] is True
    assert (answered.start, answered.end) == (fx.at(25), fx.at(28))
    assert attr.CLOCK_SKEW not in answered.attributes
    assert (open_.end, open_.attributes[attr.ASK_ANSWERED]) == (fx.at(40), False)
    assert root.attributes[attr.WAIT_ASK_MS] == 0 + 3000 + 14000


def test_pause_runs_to_its_lift_or_the_step_end() -> None:
    facts = fx.make_facts(
        pauses=(
            PauseRecord("p0", True, fx.at(2)),
            PauseRecord("p1", True, fx.at(15)),
            PauseRecord("p2", False, fx.at(18)),
            PauseRecord("p3", True, fx.at(35)),
        ),
        transitions=(fx.to("g1", "review", 40, 1),),
        **fx.runner_epoch(1, 10),
    )
    root, first, second = _step(facts)
    assert (first.name, first.start, first.end) == ("pause", fx.at(15), fx.at(18))
    assert (second.start, second.end) == (fx.at(35), fx.at(40))
    assert root.attributes[attr.WAIT_PAUSE_MS] == 8000


def test_hub_step_correlates_polls_and_slots_by_node_and_window_then_a_bounce() -> None:
    facts = fx.hub_facts(
        hub_polls=(
            HubPollRecord("h1", "g1-poll", 1, fx.at(20)),
            HubPollRecord("h2", "g1-poll", 1, fx.at(40)),
            HubPollRecord("h3", "g1-other", 1, fx.at(41)),
            HubPollRecord("h4", "g1-poll", 1, fx.at(105)),
        ),
        hub_exec_slots=(
            HubExecSlotRecord("s1", "g1-poll", fx.at(10), fx.at(30)),
            HubExecSlotRecord("s2", "g1-poll", fx.at(50), fx.at(500)),
            HubExecSlotRecord("s3", "g1-other", fx.at(12), fx.at(13)),
        ),
        bounces=(BounceRecord(2, "conflict", fx.at(90)),),
        transitions=(fx.to("g1", "build", 100, 2),),
    )
    root, first, second = _step(facts)
    assert root.name == "step poll"
    assert root.attributes[shared.NODE_EXECUTOR] == "hub"
    assert attr.RUNNER_ID not in root.attributes
    assert [e.name for e in root.events] == ["hub poll pending", "hub poll pending", "bounce"]
    assert root.events[-1].attributes[attr.BOUNCE_CAUSE] == "conflict"
    assert (first.name, first.start, first.end) == ("hub exec", fx.at(10), fx.at(30))
    assert (second.start, second.end) == (fx.at(50), fx.at(100))
    assert first.context != second.context


def test_a_hub_step_exited_in_the_write_that_minted_its_lease_still_stands_on_its_node() -> None:
    facts = fx.hub_facts(transitions=(fx.to("g1", "build", 90, 2),))
    (root,) = _step(facts)
    assert root.name == "step poll"
    assert (root.start, root.end) == (fx.at(5), fx.at(90))


def test_events_after_the_step_end_are_clamped_to_it() -> None:
    facts = fx.make_facts(
        usage=(fx.usage(at_seconds=99),), transitions=(fx.to("g1", "review", 30, 1),), **fx.runner_epoch(1, 10)
    )
    (root,) = _step(facts)
    assert root.events[0].time == fx.at(30)


def test_escalation_at_the_bounce_cap_is_an_error_root() -> None:
    facts = fx.hub_facts(
        bounces=(BounceRecord(2, "checks", fx.at(95)),),
        escalations=(EscalationRecord(2, fx.at(96)),),
    )
    (root,) = _step(facts)
    assert root.status is SpanStatus.ERROR
    assert root.attributes[attr.STEP_OUTCOME] == "escalated"
    assert root.attributes[attr.BOUNCE_CAUSE] == "checks"


def test_migration_and_migration_landing_on_a_hub_node() -> None:
    facts = fx.make_facts(
        migrations=(MigrationRecord(1, fx.at(30), "g1", "g2", from_node_id="g1-build", choice_name="upgrade"),),
        **fx.runner_epoch(1, 10),
    )
    (root,) = _step(facts)
    assert root.attributes[attr.STEP_OUTCOME] == "migrated"
    assert root.attributes[attr.STEP_TO_NODE_NAME] == "graph:flow"
    assert root.attributes[attr.STEP_CHOICE] == "upgrade"
    landed = StepFacts(
        chunk_id="ch_1",
        graphs={"g1": fx.G1, "g2": fx.hub_graph("g2")},
        pin_graph_id="g1",
        migrations=(MigrationRecord(1, fx.at(30), "g1", "g2", landed_node_id="g2-poll"),),
        **fx.runner_epoch(1, 10),
    )
    assert _step(landed)[0].attributes[attr.STEP_OUTCOME] == "migrated"


@pytest.mark.parametrize(
    ("extra", "outcome"),
    [
        ({"route_released": (RouteReleasedRecord(fx.at(30)),)}, "released"),
        ({"chunk_stopped": (ChunkStoppedRecord(fx.at(30)),)}, "stopped"),
        ({"chunk_completed": (ChunkCompletedRecord(fx.at(30)),)}, "completed"),
        (
            {"restarts": (RestartRecord(1, fx.at(30), "g1", "g1-build", decision_id=None),)},
            None,
        ),
    ],
)
def test_ending_without_a_move(extra: dict[str, tuple[object, ...]], outcome: str | None) -> None:
    facts = fx.make_facts(**fx.merge(fx.runner_epoch(1, 10), extra))
    if outcome is None:
        with pytest.raises(ValueError, match="open"):
            _step(facts)
        return
    (root,) = _step(facts)
    assert root.attributes[attr.STEP_OUTCOME] == outcome
    assert root.end == fx.at(30)
    assert root.status is SpanStatus.UNSET


def test_a_restart_closes_the_decision_and_labels_the_next_step() -> None:
    facts = fx.make_facts(
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        restarts=(RestartRecord(2, fx.at(40), "g1", "g1-build", decision_id="d1"),),
        epoch_owners=(
            EpochOwnerRecord(1, "r-1", fx.at(9)),
            EpochOwnerRecord(2, None, fx.at(40)),
            EpochOwnerRecord(3, "r-1", fx.at(49)),
        ),
        lease_facts=(LeaseRecord(1, fx.at(10)), LeaseRecord(3, fx.at(50))),
        transitions=(fx.to("g1", "review", 70, 3),),
    )
    told = _told(facts)
    assert told["ch_1/1/gate/d1"][0].attributes[attr.STEP_OUTCOME] == "restarted"
    assert told["ch_1/3"][0].attributes[attr.STEP_PRECEDED_BY] == "restart"


def test_step_cost_sums_to_the_usage_fold_and_measures_stay_on_roots() -> None:
    rows = (
        fx.usage(epoch=1, at_seconds=11, cost_usd=0.5),
        fx.usage(epoch=1, at_seconds=12, cost_usd=None, estimated_cost_usd=0.25),
        fx.usage(epoch=1, at_seconds=13, cost_usd=None),
        fx.usage(epoch=2, at_seconds=60, cost_usd=1.0, estimated_cost_usd=0.5),
    )
    facts = fx.make_facts(
        usage=rows,
        questions=(QuestionRecord("q1", 1, fx.at(15), fx.at(16)),),
        transitions=(fx.to("g1", "review", 40, 1), fx.to("g1", "gate", 90, 2)),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
    )
    told = _told(facts)
    roots = [spans[0] for spans in told.values()]
    total = UsageTotal.of(list(rows))
    assert sum(r.attributes[attr.STEP_COST_USD] for r in roots) == pytest.approx(  # type: ignore[misc]
        total.cost_usd + (total.estimated_cost_usd or 0)
    )
    first = roots[0].attributes
    assert first[attr.STEP_COST_ESTIMATED] is True
    assert first[attr.STEP_COST_PARTIAL] is True
    assert roots[1].attributes[attr.STEP_COST_PARTIAL] is False
    measures = {k for k in attr.DECLARED_ATTRIBUTES if k.startswith(("blizzard.step.wait.", "blizzard.step.cost."))}
    for spans in told.values():
        for child in spans[1:]:
            assert not measures & set(child.attributes)
            assert attr.STEP_INPUT_TOKENS not in child.attributes
    invocation_costs = [e.attributes.get(shared.INVOCATION_COST_USD) for e in told["ch_1/1"][0].events]
    assert invocation_costs == [0.5, 0.25, None]


def test_gen_ai_input_includes_cached_tokens_and_blizzard_input_stays_uncached() -> None:
    facts = fx.make_facts(usage=(fx.usage(),), transitions=(fx.to("g1", "review", 30, 1),), **fx.runner_epoch(1, 10))
    event = _step(facts)[0].events[0]
    a = event.attributes
    assert a[shared.GEN_AI_INPUT_TOKENS] == 100 + 1000 + 10
    assert a[shared.GEN_AI_CACHE_READ_TOKENS] == 1000
    assert a[shared.GEN_AI_CACHE_CREATE_TOKENS] == 10
    assert a[shared.GEN_AI_OUTPUT_TOKENS] == 50
    assert a[shared.INVOCATION_INPUT_TOKENS] == 100
    assert a[shared.GEN_AI_RESPONSE_MODEL] == "claude-x"
    assert a[shared.HARNESS_VERSION] == "2.0"


def _chain(first_close: dict[str, tuple[object, ...]], **extra: object) -> StepFacts:
    return fx.make_facts(
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50), first_close, extra),
    )


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (
            fx.make_facts(
                transitions=(fx.to("g1", "review", 30, 1),),
                restarts=(RestartRecord(5, fx.at(35), "g1", "g1-review"),),
                epoch_owners=(
                    EpochOwnerRecord(1, "r-1", fx.at(9)),
                    EpochOwnerRecord(5, None, fx.at(35)),
                    EpochOwnerRecord(6, "r-1", fx.at(49)),
                ),
                lease_facts=(LeaseRecord(1, fx.at(10)), LeaseRecord(6, fx.at(50))),
            ),
            "restart",
        ),
        (
            fx.make_facts(
                migrations=(MigrationRecord(1, fx.at(30), "g1", "g2", from_node_id="g1-build"),),
                **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
            ),
            "migration",
        ),
        (
            fx.make_facts(
                transitions=(fx.to("g1", "review", 30, 1),),
                bounces=(BounceRecord(1, "checks", fx.at(29)),),
                **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
            ),
            "bounce",
        ),
        (
            fx.make_facts(
                route_released=(RouteReleasedRecord(fx.at(30)),),
                **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
            ),
            "retry",
        ),
        (
            fx.make_facts(
                transitions=(fx.to("g1", "review", 30, 1),),
                **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
            ),
            "next",
        ),
    ],
)
def test_link_reason_and_derived_context(facts: StepFacts, reason: str) -> None:
    last = identify_steps(facts)[1].epoch
    facts = replace(facts, transitions=(*facts.transitions, fx.to("g1", "gate", 90, last)))
    steps = identify_steps(facts)
    assert _step(facts, 0)[0].links == ()
    spans = assemble_step(facts, steps[1])
    (link,) = spans[0].links
    assert link.attributes[attr.LINK_REASON] == reason
    assert link.context == DerivedContext.of(steps[0].key, SpanRole.STEP)


def test_a_link_to_a_gate_uses_the_gate_role() -> None:
    facts = fx.make_facts(
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        transitions=(fx.to("g1", "build", 60, 2, decision_id="d1"), fx.to("g1", "gate", 90, 2)),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 70)),
    )
    steps = identify_steps(facts)
    gate_index = next(i for i, s in enumerate(steps) if s.kind is StepKind.GATE)
    nxt = assemble_step(facts, steps[gate_index + 1])
    assert nxt[0].links[0].context == DerivedContext.of(steps[gate_index].key, SpanRole.GATE)


def test_an_open_step_is_refused() -> None:
    facts = fx.make_facts(**fx.runner_epoch(1, 10))
    with pytest.raises(ValueError, match="open"):
        _step(facts)


def test_planted_content_never_leaves_and_every_key_is_declared() -> None:
    base = fx.graph("g1", "build", "review", "gate")
    nodes = [replace(n, prompt=SENTINEL, checks=[SENTINEL], judgement_prompt=SENTINEL) for n in base.nodes]
    planted = replace(base, nodes=nodes)
    facts = StepFacts(
        chunk_id="ch_1",
        graphs={"g1": planted, "g2": fx.G2},
        pin_graph_id="g1",
        work_refs=("blizzard#1",),
        usage=(fx.usage(),),
        questions=(QuestionRecord("q1", 1, fx.at(15), fx.at(16)),),
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(DecisionResolutionRecord("d1", fx.at(40), choice="approve"),),
        transitions=(fx.to("g1", "review", 90, 2, decision_id="d1", choice_name="approve"),),
        bounces=(BounceRecord(1, "conflict", fx.at(18)),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 70)),
    )
    told = _told(facts)
    assert told
    for spans in told.values():
        for span in spans:
            assert set(span.attributes) <= attr.DECLARED_ATTRIBUTES
            blobs = [span.name, *map(repr, span.attributes.values())]
            for event in span.events:
                assert set(event.attributes) <= attr.DECLARED_ATTRIBUTES
                blobs += [event.name, *map(repr, event.attributes.values())]
            for link in span.links:
                assert set(link.attributes) <= attr.DECLARED_ATTRIBUTES
                blobs += map(repr, link.attributes.values())
            assert SENTINEL not in " ".join(blobs)


def test_resource_defaults_the_service_name_only_when_no_variable_names_one() -> None:
    base = attr.resource_attributes({}, "1.2.3")
    assert base == {"service.name": "blizzard-hub", "service.version": "1.2.3", "blizzard.trace.schema_version": "3"}
    assert attr.resource_attributes({"OTEL_SERVICE_NAME": "mine"}, "v")["service.name"] == "mine"
    named = {"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=other"}
    assert attr.resource_attributes(named, "v")["service.name"] == "other"
    unrelated = {"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod"}
    assert attr.resource_attributes(unrelated, "v")["service.name"] == "blizzard-hub"
    assert attr.INSTRUMENTATION_SCOPE == "blizzard.hub.fleet_spans"


def test_requeue_marks_claimable_and_unrelated_helpers_import() -> None:
    facts = fx.make_facts(
        requeues=(RequeueRecord(fx.at(8)),),
        routes_created=(RouteCreatedRecord(fx.at(12)),),
        transitions=(fx.to("g1", "review", 50, 1),),
        **fx.runner_epoch(1, 14),
    )
    queue = _by_name(_step(facts))["queue wait"]
    assert (queue.start, queue.end) == (fx.at(8), fx.at(12))


def test_a_move_back_onto_the_same_node_is_next_not_retry() -> None:
    facts = fx.make_facts(
        transitions=(fx.to("g1", "build", 30, 1), fx.to("g1", "gate", 90, 2)),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
    )
    steps = identify_steps(facts)
    (link,) = assemble_step(facts, steps[1])[0].links
    assert link.attributes[attr.LINK_REASON] == "next"


def test_a_hub_node_step_names_the_hub_executor() -> None:
    facts = fx.hub_facts(transitions=(fx.to("g1", "build", 100, 2),))
    root = _step(facts)[0]
    assert root.attributes[shared.NODE_EXECUTOR] == "hub"
    assert attr.RUNNER_ID not in root.attributes


def test_gate_choice_comes_from_the_resolution() -> None:
    facts = fx.make_facts(
        decisions=(DecisionRecord("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(DecisionResolutionRecord("d1", fx.at(40), choice="approve"),),
        transitions=(fx.to("g1", "build", 500, 2, decision_id="d1"),),
        **fx.runner_epoch(1, 10),
    )
    root = _step(facts, 1)[0]
    assert root.attributes[attr.STEP_CHOICE] == "approve"
