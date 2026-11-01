"""What a landing policy is — one operation, stepping one repository to where it stands — and the policy each
:class:`LandingPolicy` names. What a policy requires of its forge is not its own to say: that is
``POLICY_CAPABILITIES``, beside the policies' names."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from blizzard.hub.delivery.actions.landing.fast_forward import FastForward
from blizzard.hub.delivery.actions.landing.merge_on_checks import MergeOnChecks
from blizzard.hub.delivery.actions.landing.park import Park
from blizzard.hub.delivery.actions.landing.repo import Admitted, RepoLanding, RepoState
from blizzard.hub.domain.config.landing_policies import LandingPolicy


class ILandingPolicy(Protocol):
    async def step(self, repo: RepoLanding) -> RepoState | Admitted:
        """Where ``repo`` stands this pass — or, its head proven, :class:`Admitted` to finish once every other head
        is; every marker it writes is handed to the pass's effects."""
        ...


POLICIES: Mapping[LandingPolicy, ILandingPolicy] = {
    LandingPolicy.MERGE_ON_CHECKS: MergeOnChecks(),
    LandingPolicy.FAST_FORWARD: FastForward(),
    LandingPolicy.PARK: Park(),
}
