"""The step summary (unit tier) — the one computation a span tree and an egress row both map."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.tracing.steps import StepKind, identify_steps
from blizzard.hub.domain.tracing.summary import IntervalKind, StepSummary, summarize_step
from tests import trace_fixtures as fx

pytestmark = pytest.mark.unit

_WAIT_ATTRS = {
    "queue wait": attr.WAIT_QUEUE_MS,
    "claim": attr.WAIT_CLAIM_MS,
    "ask": attr.WAIT_ASK_MS,
    "pause": attr.WAIT_PAUSE_MS,
    "decision pickup": attr.WAIT_PICKUP_MS,
}


def _closed(facts: StepFacts) -> list[StepSummary]:
    steps = identify_steps(facts)
    return [summarize_step(facts, s, steps) for s in steps if s.close is not None]


def _every_closed_step() -> list[tuple[str, StepFacts, int]]:
    out: list[tuple[str, StepFacts, int]] = []
    for name, facts in fx.scenarios().items():
        out += [(name, facts, i) for i, s in enumerate(identify_steps(facts)) if s.close is not None]
    return out


EVERY_STEP = _every_closed_step()
IDS = [f"{name}:{i}" for name, _, i in EVERY_STEP]


def test_the_shared_scenarios_close_at_least_one_step_each() -> None:
    assert {name for name, _, _ in EVERY_STEP} == set(fx.scenarios())


@pytest.mark.parametrize(
    ("name", "expected_billed", "expected_estimated"),
    [
        ("cost-billed-only", 0.625, None),
        ("cost-estimated-only", None, 0.25),
        ("cost-neither", None, None),
        ("no-invocations", None, None),
    ],
)
def test_billed_and_estimated_cost_are_each_null_when_absent(
    name: str, expected_billed: float | None, expected_estimated: float | None
) -> None:
    (summary,) = _closed(fx.scenarios()[name])
    assert summary.cost_billed_usd == expected_billed
    assert summary.cost_estimated_usd == expected_estimated


@pytest.mark.parametrize(("name", "index"), [(n, i) for n, _, i in EVERY_STEP], ids=IDS)
def test_the_folded_cost_equals_the_span_roots(name: str, index: int) -> None:
    facts = fx.scenarios()[name]
    step = identify_steps(facts)[index]
    root = assemble_step(facts, step)[0]
    summary = summarize_step(facts, step)
    assert summary.folded_cost_usd() == pytest.approx(root.attributes[attr.STEP_COST_USD])  # type: ignore[arg-type]


@pytest.mark.parametrize(("name", "index"), [(n, i) for n, _, i in EVERY_STEP], ids=IDS)
def test_each_wait_equals_the_sum_of_the_matching_children(name: str, index: int) -> None:
    facts = fx.scenarios()[name]
    step = identify_steps(facts)[index]
    root, *children = assemble_step(facts, step)
    summary = summarize_step(facts, step)
    for span_name, key in _WAIT_ATTRS.items():
        summed = sum(round((c.end - c.start).total_seconds() * 1000) for c in children if c.name == span_name)
        assert root.attributes[key] == summed
    assert summary.wait_queue_ms == root.attributes[attr.WAIT_QUEUE_MS]
    assert summary.wait_claim_ms == root.attributes[attr.WAIT_CLAIM_MS]
    assert summary.wait_ask_ms == root.attributes[attr.WAIT_ASK_MS]
    assert summary.wait_pause_ms == root.attributes[attr.WAIT_PAUSE_MS]
    assert summary.wait_pickup_ms == root.attributes[attr.WAIT_PICKUP_MS]
    assert len(summary.intervals) == len(children)


def test_a_gate_ends_at_its_resolution_and_closes_at_the_closing_fact() -> None:
    facts = fx.scenarios()["gate-resolved-late-pickup"]
    gate = next(s for s in _closed(facts) if s.kind is StepKind.GATE)
    assert gate.ended_at == fx.at(40)
    assert gate.closed_at == fx.at(500)
    assert gate.choice == "approve"
    assert [i.kind for i in gate.intervals] == [IntervalKind.PICKUP]


def test_asks_count_the_unanswered_and_a_skewed_ask_is_clamped() -> None:
    (summary,) = _closed(fx.scenarios()["asks"])
    assert (summary.asks, summary.asks_unanswered) == (3, 1)
    assert next(i for i in summary.intervals if i.discriminator == "q1").clock_skew is True


def test_an_open_step_is_refused() -> None:
    facts = fx.make_facts(**fx.runner_epoch(1, 10))
    with pytest.raises(ValueError, match="open"):
        summarize_step(facts, identify_steps(facts)[0])
