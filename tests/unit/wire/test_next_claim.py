"""The next-claim wire's target-free scenario is additive: a body without it parses as unreported."""

from __future__ import annotations

from blizzard.wire.queue import NextClaimResponse


def test_a_body_without_the_target_free_scenario_parses_as_not_reported() -> None:
    parsed = NextClaimResponse.model_validate({"next": None, "passed": [], "passed_total": 0, "unserved": 0})

    assert parsed.without_repository_targets is None


def test_a_reported_scenario_carries_its_excluded_count() -> None:
    parsed = NextClaimResponse.model_validate({"without_repository_targets": {"excluded": 2}})

    assert parsed.without_repository_targets is not None
    assert parsed.without_repository_targets.excluded == 2
