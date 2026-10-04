"""The cost-withholding rule as domain policy: what a usage fact persists for a sample, read against its session's banked basis."""

from __future__ import annotations

from dataclasses import replace

import pytest

from blizzard.runner.harness.usage import SessionCostBasis, UsageSample
from blizzard.runner.usage.repository import InvocationCost, derive_invocation_cost

pytestmark = pytest.mark.unit


def _sample(cost: float | None = 1.5) -> UsageSample:
    return UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=3,
        cache_create_tokens=4,
        cost_usd=cost,
    )


def test_a_first_billed_reading_passes_through_with_its_estimate() -> None:
    sample = replace(_sample(cost=1.5), estimated_cost_usd=0.4)

    assert derive_invocation_cost(sample, None) == InvocationCost(cost_usd=1.5, estimated_cost_usd=0.4)


def test_an_unbilled_sample_keeps_its_estimate() -> None:
    sample = replace(_sample(cost=None), estimated_cost_usd=0.0308)

    assert derive_invocation_cost(sample, None) == InvocationCost(cost_usd=None, estimated_cost_usd=0.0308)


def test_a_session_scoped_figure_is_read_as_this_invocations_share() -> None:
    sample = replace(_sample(cost=8.0), cost_scope_tokens=74)
    basis = SessionCostBasis(token_total=37, banked_cost_usd=5.0)

    assert derive_invocation_cost(sample, basis).cost_usd == pytest.approx(3.0)


def test_a_rejected_billed_reading_withholds_the_estimate_too() -> None:
    """A billed reading ``invocation_cost`` rejects as backwards leaves ``cost_usd`` ``None``;
    an estimate riding the same sample must not survive it either, or the two would disagree."""
    sample = replace(_sample(cost=3.0), cost_scope_tokens=74, estimated_cost_usd=0.02)
    basis = SessionCostBasis(token_total=37, banked_cost_usd=5.0)

    assert derive_invocation_cost(sample, basis) == InvocationCost(cost_usd=None, estimated_cost_usd=None)
