"""The implementation registry names every landing policy exactly once."""

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.hub.delivery.actions.landing.fast_forward import FastForward
from blizzard.hub.delivery.actions.landing.merge_on_checks import MergeOnChecks
from blizzard.hub.delivery.actions.landing.park import Park
from blizzard.hub.delivery.actions.landing.policy import POLICIES, WATCHERS
from blizzard.hub.domain.config.landing_policies import POLICY_FACTS


def test_every_policy_names_its_implementation() -> None:
    assert set(POLICIES) == set(LandingPolicy)
    assert isinstance(POLICIES[LandingPolicy.PARK], Park)
    assert isinstance(POLICIES[LandingPolicy.FAST_FORWARD], FastForward)
    assert isinstance(POLICIES[LandingPolicy.MERGE_ON_CHECKS], MergeOnChecks)


def test_watchers_match_the_watching_facts_and_share_the_policy_instances() -> None:
    assert set(WATCHERS) == {policy for policy, facts in POLICY_FACTS.items() if facts.watches}
    assert set(WATCHERS) == {LandingPolicy.PARK}
    for policy in WATCHERS:
        assert WATCHERS[policy] is POLICIES[policy]
