"""How work lands into a repository: the closed set of landing policies, what each needs of its forge, and the one rule
a project's repositories are ordered by.

The module sits in ``config``, which imports no other domain package, so the repository record, the configuration
writer's capability check, the actions' requirements, and the ``land`` action all read the same table."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.errors import ConfigFieldError


class LandingPolicy(StrEnum):
    """How a repository's changes land."""

    MERGE_ON_CHECKS = "merge-on-checks"


#: The policy a repository lands by when none is stated — the store's server default too.
DEFAULT_LANDING_POLICY = LandingPolicy.MERGE_ON_CHECKS

#: How long a parked change may wait when its repository states no timeout.
DEFAULT_PARK_TIMEOUT_HOURS = 168

#: The policies a park timeout belongs to; a timeout on a repository landing by any other is refused.
PARKING_POLICIES: frozenset[LandingPolicy] = frozenset()


@domain_model
@dataclass(frozen=True)
class PolicyCapabilities:
    """What a policy cannot land without, and what it uses when its forge offers it."""

    requires: frozenset[Capability]
    uses: frozenset[Capability] = frozenset()


POLICY_CAPABILITIES: Mapping[LandingPolicy, PolicyCapabilities] = {
    LandingPolicy.MERGE_ON_CHECKS: PolicyCapabilities(
        requires=frozenset(
            {
                Capability.REFS_READ,
                Capability.COMPARE,
                Capability.CHANGES_OPEN,
                Capability.CHANGES_READ,
                Capability.CHANGES_UPDATE_FROM_BASE,
                Capability.CHANGES_MERGE_MERGE,
                Capability.CHECKS_READ,
            }
        ),
        uses=frozenset({Capability.CHECKS_RERUN}),
    ),
}


class ForgeCapabilityMissing(ConfigFieldError):
    """A repository's policy needs capabilities its forge does not offer; names each one, the policy, and the forge."""

    code = "forge_capability_missing"

    def __init__(self, policy: LandingPolicy, forge: str, missing: Iterable[Capability]) -> None:
        self.missing = tuple(sorted(missing))
        names = ", ".join(self.missing)
        super().__init__("landing_policy", f"policy {policy} needs {names}, which forge {forge} doesn't offer")


def parse_policy(raw: str) -> LandingPolicy:
    """:class:`ConfigFieldError` on ``landing_policy`` when ``raw`` names no policy."""
    try:
        return LandingPolicy(raw)
    except ValueError:
        known = ", ".join(policy.value for policy in LandingPolicy)
        raise ConfigFieldError("landing_policy", f"unknown policy {raw!r}; expected one of {known}") from None


def require_offered(policy: LandingPolicy, offered: frozenset[Capability], *, forge: str) -> None:
    """:class:`ForgeCapabilityMissing` when ``offered`` lacks any capability ``policy`` requires."""
    missing = POLICY_CAPABILITIES[policy].requires - offered
    if missing:
        raise ForgeCapabilityMissing(policy, forge, missing)


def landing_sort_key(position: float | None, slug: str) -> tuple[bool, float, str]:
    """Where a repository sorts in its project's landing order: position ascending, unranked after every ranked one,
    and any tie — unranked among themselves, or a shared number — by slug."""
    return (position is None, 0.0 if position is None else position, slug)
