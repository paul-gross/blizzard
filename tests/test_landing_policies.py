"""The landing policies' table, the capability check, and the landing order's sort key (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.landing_policies import (
    POLICY_CAPABILITIES,
    ForgeCapabilityMissing,
    LandingPolicy,
    landing_sort_key,
    parse_policy,
    require_offered,
)

pytestmark = pytest.mark.unit


def _sorted(entries: list[tuple[str, float | None]]) -> list[str]:
    return [slug for slug, _ in sorted(entries, key=lambda entry: landing_sort_key(entry[1], entry[0]))]


def test_ranked_repositories_land_by_position_ascending() -> None:
    assert _sorted([("web", 2.0), ("api", 3.0), ("lib", 1.0)]) == ["lib", "web", "api"]


def test_an_unranked_repository_lands_after_every_ranked_one() -> None:
    assert _sorted([("aaa", None), ("zzz", 1000.0), ("mmm", -5.0)]) == ["mmm", "zzz", "aaa"]


def test_unranked_repositories_land_by_slug() -> None:
    assert _sorted([("blizzard-mock", None), ("blizzard", None), ("blizzard-context", None)]) == [
        "blizzard",
        "blizzard-context",
        "blizzard-mock",
    ]


def test_a_shared_position_is_broken_by_slug() -> None:
    assert _sorted([("web", 1.0), ("api", 1.0)]) == ["api", "web"]


def test_merge_on_checks_requires_the_pull_request_capabilities_and_uses_a_rerun() -> None:
    capabilities = POLICY_CAPABILITIES[LandingPolicy.MERGE_ON_CHECKS]
    assert capabilities.requires == {
        Capability.REFS_READ,
        Capability.COMPARE,
        Capability.CHANGES_OPEN,
        Capability.CHANGES_READ,
        Capability.CHANGES_UPDATE_FROM_BASE,
        Capability.CHANGES_MERGE_MERGE,
        Capability.CHECKS_READ,
    }
    assert capabilities.uses == {Capability.CHECKS_RERUN}


def test_every_policy_has_its_capabilities() -> None:
    assert set(POLICY_CAPABILITIES) == set(LandingPolicy)


def test_a_forge_offering_every_requirement_passes() -> None:
    require_offered(LandingPolicy.MERGE_ON_CHECKS, frozenset(Capability), forge="github")


def test_a_forge_lacking_requirements_is_refused_naming_each_policy_and_forge() -> None:
    offered = frozenset(Capability) - {Capability.CHANGES_MERGE_MERGE, Capability.CHECKS_READ}
    with pytest.raises(ForgeCapabilityMissing) as refused:
        require_offered(LandingPolicy.MERGE_ON_CHECKS, offered, forge="gitlab")
    assert refused.value.code == "forge_capability_missing"
    assert refused.value.field == "landing_policy"
    assert refused.value.missing == (Capability.CHANGES_MERGE_MERGE, Capability.CHECKS_READ)
    for named in ("changes.merge.merge", "checks.read", "merge-on-checks", "gitlab"):
        assert named in refused.value.message


def test_parse_policy_refuses_an_unknown_name() -> None:
    assert parse_policy("merge-on-checks") is LandingPolicy.MERGE_ON_CHECKS
    with pytest.raises(ConfigFieldError) as refused:
        parse_policy("merge-queue")
    assert refused.value.field == "landing_policy"
