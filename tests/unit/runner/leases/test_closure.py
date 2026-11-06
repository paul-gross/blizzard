"""The closure-reason partition: every reason a lease can close with is classified as spent, unspent or a visit
boundary, exactly once (unit tier)."""

from __future__ import annotations

from itertools import combinations

from blizzard.runner.leases import closure


def test_every_closure_reason_is_classified_exactly_once() -> None:
    sets = [closure.SPENT_REASONS, closure.UNSPENT_REASONS, closure.COMPLETED_STEP_REASONS]

    for left, right in combinations(sets, 2):
        assert left.isdisjoint(right)
    assert frozenset().union(*sets) == closure.PUBLISHED_REASONS | closure.MINT_REASONS
