"""The ``steps`` and ``invocations`` rows (unit tier) — facts built directly, no store."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.hub.domain.observability.egress.rows import (
    AttributedUsage,
    ExportedInvocation,
    ExportedStep,
    invocation_row,
    money,
    step_row,
)
from blizzard.hub.domain.observability.tracing import attributes as attr
from blizzard.hub.domain.observability.tracing.assembly import assemble_step
from blizzard.hub.domain.observability.tracing.facts import (
    StepFacts,
    TracedDecision,
    TracedDecisionResolution,
    TracedQuestion,
)
from blizzard.hub.domain.observability.tracing.steps import StepKind, StepOutcome, identify_steps
from blizzard.hub.domain.observability.tracing.summary import StepSummary, summarize_step
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL-do-not-leak"
EXPORTED = datetime(2026, 2, 1, tzinfo=UTC)

STEP_COLUMNS = [
    "step_key", "trace_id", "step_kind", "chunk_id", "work_refs", "sources", "graph_id", "graph_name", "node_id",
    "node_name", "epoch", "decision_id", "visit", "runner_id", "harness_id", "models", "started_at", "ended_at",
    "closed_at", "duration_ms", "outcome", "choice", "to_node_name", "preceded_by", "bounce_cause", "asks",
    "asks_unanswered", "wait_queue_ms", "wait_claim_ms", "wait_ask_ms", "wait_pause_ms", "wait_pickup_ms",
    "invocations", "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens", "cost_billed_usd",
    "cost_estimated_usd", "cost_partial", "billed_partial", "exported_at",
]  # fmt: skip
INVOCATION_COLUMNS = [
    "usage_id", "step_key", "trace_id", "chunk_id", "epoch", "graph_id", "graph_name", "node_id", "node_name",
    "runner_id", "kind", "model", "harness_id", "harness_version", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_create_tokens", "cost_billed_usd", "cost_estimated_usd", "recorded_at", "exported_at",
]  # fmt: skip


def _summaries(facts: StepFacts) -> list[StepSummary]:
    steps = identify_steps(facts)
    return [summarize_step(facts, s, steps) for s in steps if s.close is not None]


def _all() -> list[tuple[str, StepSummary]]:
    return [(name, summary) for name, facts in fx.scenarios().items() for summary in _summaries(facts)]


def _usage_row(fact: object, usage_id: int = 1, chunk_id: str = "ch_1") -> AttributedUsage:
    return AttributedUsage(usage_id, chunk_id, "r-1", fact)  # type: ignore[arg-type]


def test_row_fields_are_the_contract_columns_in_order() -> None:
    assert [f.name for f in fields(ExportedStep)] == STEP_COLUMNS
    assert [f.name for f in fields(ExportedInvocation)] == INVOCATION_COLUMNS


@pytest.mark.parametrize(("name", "summary"), _all(), ids=lambda v: v if isinstance(v, str) else v.step_key.text())
def test_a_steps_row_only_renames_its_summary(name: str, summary: StepSummary) -> None:
    row = step_row(summary, EXPORTED)
    assert row.step_key == summary.step_key.text()
    assert row.trace_id == f"{step_root(summary.step_key).trace_id:032x}"
    assert len(row.trace_id) == 32
    assert row.step_kind == summary.kind.value
    assert row.sources == summary.work_sources
    assert row.work_refs == summary.work_refs
    assert (row.graph_id, row.graph_name, row.node_id, row.node_name) == (
        summary.graph_id,
        summary.graph_name,
        summary.node_id,
        summary.node_name,
    )
    assert row.runner_id == (summary.runner_id if summary.kind is StepKind.RUNNER else None)
    assert row.outcome == summary.outcome.value
    assert row.duration_ms == round((row.ended_at - row.started_at).total_seconds() * 1000)
    assert (row.started_at, row.ended_at, row.closed_at) == (summary.started_at, summary.ended_at, summary.closed_at)
    assert row.exported_at == EXPORTED
    for column in (
        "epoch", "decision_id", "visit", "harness_id", "models", "choice", "to_node_name", "bounce_cause", "asks",
        "asks_unanswered", "wait_queue_ms", "wait_claim_ms", "wait_ask_ms", "wait_pause_ms", "wait_pickup_ms",
        "invocations", "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens", "cost_partial",
        "billed_partial",
    ):  # fmt: skip
        assert getattr(row, column) == getattr(summary, column), column
    assert money(summary.cost_billed_usd) == row.cost_billed_usd
    assert money(summary.cost_estimated_usd) == row.cost_estimated_usd


def test_the_scenarios_cover_every_step_kind_and_every_outcome() -> None:
    summaries = [s for _, s in _all()]
    assert {s.kind for s in summaries} == set(StepKind)
    assert {s.outcome for s in summaries} == set(StepOutcome)


def test_a_gate_resolved_long_before_its_pickup() -> None:
    gate = next(s for s in _summaries(fx.scenarios()["gate-resolved-late-pickup"]) if s.kind is StepKind.GATE)
    row = step_row(gate, EXPORTED)
    assert (row.started_at, row.ended_at, row.closed_at) == (fx.at(20), fx.at(40), fx.at(500))
    assert row.duration_ms == 20_000
    assert row.wait_pickup_ms == 460_000
    assert (row.runner_id, row.decision_id, row.choice) == (None, "d1", "approve")


def test_hub_and_gate_rows_carry_no_runner_but_runner_rows_do() -> None:
    by_kind = {s.kind: step_row(s, EXPORTED) for _, s in _all()}
    assert by_kind[StepKind.RUNNER].runner_id == "r-1"
    assert by_kind[StepKind.HUB].runner_id is None
    assert by_kind[StepKind.GATE].runner_id is None


def test_a_migration_and_a_restart_row() -> None:
    (migrated,) = _summaries(fx.scenarios()["migrated"])
    assert (step_row(migrated, EXPORTED).outcome, step_row(migrated, EXPORTED).to_node_name) == (
        "migrated",
        "graph:flow",
    )
    rows = {s.step_key.text(): step_row(s, EXPORTED) for s in _summaries(fx.scenarios()["restart"])}
    assert rows["ch_1/1/gate/d1"].outcome == "restarted"
    assert rows["ch_1/3"].preceded_by == "restart"


def test_names_after_a_repin_come_from_the_pinned_graph() -> None:
    rows = [step_row(s, EXPORTED) for s in _summaries(fx.scenarios()["repinned"])]
    first, second = rows[0], rows[1]
    assert (first.graph_id, first.graph_name) == ("g1", "legacy-flow")
    assert (second.graph_id, second.graph_name) == ("g2", "modern-flow")


@pytest.mark.parametrize(
    ("name", "billed", "estimated", "cost_partial", "billed_partial"),
    [
        ("cost-billed-only", Decimal("0.625"), None, False, False),
        ("cost-estimated-only", None, Decimal("0.25"), False, True),
        ("cost-neither", None, None, True, True),
        ("no-invocations", None, None, False, False),
    ],
)
def test_cost_columns_follow_the_spend_contract(
    name: str, billed: Decimal | None, estimated: Decimal | None, cost_partial: bool, billed_partial: bool
) -> None:
    (row,) = (step_row(s, EXPORTED) for s in _summaries(fx.scenarios()[name]))
    assert (row.cost_billed_usd, row.cost_estimated_usd) == (billed, estimated)
    assert (row.cost_partial, row.billed_partial) == (cost_partial, billed_partial)


def test_a_step_with_both_costs_keeps_them_apart() -> None:
    first = step_row(_summaries(fx.scenarios()["cost-mixed"])[0], EXPORTED)
    assert first.cost_billed_usd == Decimal("0.5")
    assert first.cost_estimated_usd == Decimal("0.25")
    assert (first.cost_partial, first.billed_partial) == (True, True)
    assert first.invocations == 3


def test_money_is_a_decimal_at_scale_nine_without_float_noise() -> None:
    assert money(0.1 + 0.2) == Decimal("0.300000000")
    assert str(money(0.5)) == "0.500000000"
    assert money(None) is None


@pytest.mark.parametrize("name", list(fx.scenarios()))
def test_row_cost_sums_to_the_span_roots_cost(name: str) -> None:
    facts = fx.scenarios()[name]
    for step in identify_steps(facts):
        if step.close is None:
            continue
        row = step_row(summarize_step(facts, step), EXPORTED)
        root = assemble_step(facts, step, identify_steps(facts))[0]
        folded = float((row.cost_billed_usd or 0) + (row.cost_estimated_usd or 0))
        assert folded == pytest.approx(root.attributes[attr.STEP_COST_USD], abs=1e-9)  # type: ignore[arg-type]


def test_an_open_steps_invocation_matches_its_row_once_it_closes() -> None:
    open_facts = fx.make_facts(
        usage=(fx.usage(node_id="g1-review"),),
        **fx.runner_epoch(1, 10),
    )
    assert next(s for s in identify_steps(open_facts)).close is None
    early = fx.invocation_of(open_facts, _usage_row(open_facts.usage[0]), EXPORTED)
    closed_facts = replace(open_facts, transitions=(fx.to("g1", "review", 30, 1),))
    (summary,) = _summaries(closed_facts)
    row = step_row(summary, EXPORTED)
    assert (early.step_key, early.trace_id) == (row.step_key, row.trace_id)
    assert early.step_key == StepKey.attempt("ch_1", 1).text()
    assert (early.graph_id, early.node_id, early.node_name) == ("g1", "g1-build", "build")
    assert fx.invocation_of(closed_facts, _usage_row(closed_facts.usage[0]), EXPORTED) == early


def test_an_invocation_row_renames_its_usage_fact() -> None:
    fact = fx.usage(cost_usd=None, estimated_cost_usd=0.25, harness_id=None, harness_version=None)
    facts = fx.make_facts(usage=(fact,), transitions=(fx.to("g1", "review", 30, 1),), **fx.runner_epoch(1, 10))
    row = fx.invocation_of(facts, AttributedUsage(7, "ch_1", "r-9", fact), EXPORTED)
    assert (row.usage_id, row.runner_id, row.kind, row.model) == (7, "r-9", "spawn", "claude-x")
    assert (row.harness_id, row.harness_version) == (None, None)
    assert (row.cost_billed_usd, row.cost_estimated_usd) == (None, Decimal("0.25"))
    assert (row.input_tokens, row.cache_read_tokens) == (100, 1000)
    assert (row.recorded_at, row.exported_at) == (fact.recorded_at, EXPORTED)
    assert len(row.trace_id) == 32


def _release_facts() -> StepFacts:
    """A chunk whose every identifying value differs from the fixtures' defaults."""
    graph = replace(fx.graph("g5", "plan", "ship"), name="release-flow")
    return fx.make_facts(chunk_id="ch_7", graphs={"g5": graph}, pin_graph_id="g5", **fx.runner_epoch(2, 40, "r-x"))


def test_an_invocation_row_in_full() -> None:
    fact = fx.usage(
        epoch=2,
        at_seconds=45,
        node_id="g5-ship",
        kind="judge",
        model="gpt-q",
        input_tokens=11,
        output_tokens=22,
        cache_read_tokens=33,
        cache_create_tokens=44,
        cost_usd=0.125,
        estimated_cost_usd=0.0625,
        harness_id="opencode",
        harness_version="0.9",
    )
    row = fx.invocation_of(_release_facts(), AttributedUsage(42, "ch_7", "r-x", fact), EXPORTED)
    assert row == ExportedInvocation(
        usage_id=42,
        step_key="ch_7/2",
        trace_id="f3e010092ca8029cd2d02a1a497f5ece",
        chunk_id="ch_7",
        epoch=2,
        graph_id="g5",
        graph_name="release-flow",
        node_id="g5-plan",
        node_name="plan",
        runner_id="r-x",
        kind="judge",
        model="gpt-q",
        harness_id="opencode",
        harness_version="0.9",
        input_tokens=11,
        output_tokens=22,
        cache_read_tokens=33,
        cache_create_tokens=44,
        cost_billed_usd=Decimal("0.125000000"),
        cost_estimated_usd=Decimal("0.062500000"),
        recorded_at=datetime(2026, 1, 1, 0, 0, 45, tzinfo=UTC),
        exported_at=datetime(2026, 2, 1, tzinfo=UTC),
    )


def test_an_invocation_of_another_chunk_is_refused_naming_both() -> None:
    facts = _release_facts()
    step = next(s for s in identify_steps(facts) if s.kind is StepKind.RUNNER)
    usage = AttributedUsage(42, "ch_other", "r-x", fx.usage(epoch=2))
    with pytest.raises(ValueError) as refused:
        invocation_row(facts, step, usage, EXPORTED)
    assert str(refused.value) == "usage 42 belongs to ch_other, not ch_7"


def test_an_invocation_without_a_runner_step_at_its_epoch_is_refused() -> None:
    facts = fx.make_facts(**fx.runner_epoch(1, 10))
    with pytest.raises(LookupError):
        fx.invocation_of(facts, _usage_row(fx.usage(epoch=4)), EXPORTED)
    with pytest.raises(ValueError, match="belongs to"):
        fx.invocation_of(facts, _usage_row(fx.usage(), chunk_id="ch_other"), EXPORTED)


def test_an_invocation_on_a_hub_epoch_is_refused() -> None:
    facts = fx.hub_facts()
    with pytest.raises(LookupError):
        fx.invocation_of(facts, _usage_row(fx.usage(epoch=2)), EXPORTED)


def _planted() -> StepFacts:
    base = fx.graph("g1", "build", "review", "gate")
    nodes = [replace(n, prompt=SENTINEL, checks=[SENTINEL], judgement_prompt=SENTINEL) for n in base.nodes]
    return StepFacts(
        chunk_id="ch_1",
        graphs={"g1": replace(base, nodes=nodes), "g2": fx.G2},
        pin_graph_id="g1",
        work_refs=("blizzard#1",),
        work_sources=("blizzard",),
        usage=(fx.usage(),),
        questions=(TracedQuestion("q1", 1, fx.at(15), fx.at(16)),),
        decisions=(TracedDecision("d1", "g1-gate", 1, fx.at(20)),),
        decision_resolutions=(TracedDecisionResolution("d1", fx.at(40), choice="approve"),),
        transitions=(fx.to("g1", "review", 90, 2, decision_id="d1", choice_name="approve"),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 70)),
    )


def scan(rows: list[ExportedStep | ExportedInvocation], needle: str) -> list[str]:
    """The column of every value, anywhere in any row, that carries ``needle``."""
    return [f.name for row in rows for f in fields(row) if needle in repr(getattr(row, f.name))]


def test_planted_content_never_reaches_a_row() -> None:
    planted = _planted()
    rows: list[ExportedStep | ExportedInvocation] = [step_row(s, EXPORTED) for s in _summaries(planted)]
    assert {r.step_kind for r in rows if isinstance(r, ExportedStep)} == {"runner", "gate"}
    rows += [fx.invocation_of(planted, _usage_row(u), EXPORTED) for u in planted.usage]
    assert len(rows) >= 3
    assert scan(rows, SENTINEL) == []
    assert scan(rows, "ch_1") != []
