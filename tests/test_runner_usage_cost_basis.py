"""Reading a harness's reported cost figure as one invocation's cost.

A harness may charge its figure against the whole session rather than the invocation
that produced it — Claude Code began doing exactly that on a ``--resume`` at 2.1.278,
having reported the invocation alone at 2.1.274. Which reading applies is settled per
envelope, against what the session has already banked, never by a version lookup."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.usage import SessionCostBasis, UsageSample, invocation_cost

pytestmark = pytest.mark.unit


def _sample(*, cost: float | None, tokens: int, scope: int | None) -> UsageSample:
    """One invocation of ``tokens`` cache-read tokens, reporting ``cost`` over ``scope``."""
    return UsageSample(
        kind="resume",
        model="claude-sonnet-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=tokens,
        cache_create_tokens=0,
        cost_usd=cost,
        cost_scope_tokens=scope,
    )


def test_a_session_scoped_figure_is_charged_as_its_delta() -> None:
    # The session banked 20_014 tokens for $0.0253; this invocation's own 25_336 bring the
    # reported scope to 45_350 — so the figure covers both, and only the difference is new.
    prior = SessionCostBasis(token_total=20_014, banked_cost_usd=0.0253248)
    sample = _sample(cost=0.030490, tokens=25_336, scope=45_350)
    assert invocation_cost(sample, prior) == pytest.approx(0.0051652)


def test_an_invocation_scoped_figure_rides_verbatim() -> None:
    # The same banked session, but the reported scope is this invocation's tokens alone.
    prior = SessionCostBasis(token_total=18_538, banked_cost_usd=0.0314736)
    sample = _sample(cost=0.005145, tokens=25_235, scope=25_235)
    assert invocation_cost(sample, prior) == pytest.approx(0.005145)


def test_an_invocation_costing_more_than_the_whole_session_so_far_is_not_read_as_cumulative() -> None:
    # The hazard a cost-only comparison falls into: this figure exceeds everything banked,
    # which a `reported >= prior` test would subtract from. Its scope says otherwise.
    prior = SessionCostBasis(token_total=1_000, banked_cost_usd=0.01)
    sample = _sample(cost=5.0, tokens=900_000, scope=900_000)
    assert invocation_cost(sample, prior) == pytest.approx(5.0)


def test_the_first_invocation_of_a_session_rides_verbatim_under_either_reading() -> None:
    nothing_banked = SessionCostBasis(token_total=0, banked_cost_usd=0.0)
    sample = _sample(cost=0.0253248, tokens=20_014, scope=20_014)
    assert invocation_cost(sample, nothing_banked) == pytest.approx(0.0253248)
    assert invocation_cost(sample, None) == pytest.approx(0.0253248)


def test_an_envelope_reporting_no_scope_rides_verbatim() -> None:
    # An adapter whose figure is this invocation's by construction — OpenCode sums the
    # steps of this capture — reports no scope, and must never be delta'd.
    prior = SessionCostBasis(token_total=50_000, banked_cost_usd=2.0)
    assert invocation_cost(_sample(cost=0.75, tokens=9_000, scope=None), prior) == pytest.approx(0.75)


def test_an_absent_cost_stays_absent() -> None:
    prior = SessionCostBasis(token_total=50_000, banked_cost_usd=2.0)
    assert invocation_cost(_sample(cost=None, tokens=9_000, scope=59_000), prior) is None


def test_a_session_scoped_figure_that_went_backwards_is_unknown_rather_than_zero() -> None:
    # No accumulating harness produces this, and PARTIAL is what a total already carries
    # for an unknown, where 0.0 would read as a measured free invocation.
    prior = SessionCostBasis(token_total=50_000, banked_cost_usd=9.0)
    assert invocation_cost(_sample(cost=4.0, tokens=10_000, scope=60_000), prior) is None


def test_a_session_scoped_figure_recovers_cost_an_earlier_envelope_never_reported() -> None:
    # An earlier invocation whose worker died banked tokens but no dollars, so the session's
    # banked total is still 0 — the next figure covers both.
    prior = SessionCostBasis(token_total=40_000, banked_cost_usd=0.0)
    assert invocation_cost(_sample(cost=1.25, tokens=10_000, scope=50_000), prior) == pytest.approx(1.25)


def test_a_scope_change_mid_session_does_not_re_bank_what_earlier_facts_carried() -> None:
    # Earlier envelopes rode verbatim — an older harness reporting no scope — so what this
    # session banked ($1.00 + $2.00 + $0.50) is a sum no single figure equals.
    prior = SessionCostBasis(token_total=30_000, banked_cost_usd=3.50)
    sample = _sample(cost=4.20, tokens=10_000, scope=40_000)
    assert invocation_cost(sample, prior) == pytest.approx(0.70)
