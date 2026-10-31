"""What each hub action needs its repositories' forges to offer, keyed by action name.

Each entry is a function of the resolved repository, so a requirement that varies by repository
changes only what an entry answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES
from blizzard.hub.domain.config.repositories import ResolvedRepository

Requirement = Callable[[ResolvedRepository], frozenset[Capability]]

_NONE: frozenset[Capability] = frozenset()


def _land(repository: ResolvedRepository) -> frozenset[Capability]:
    """What the policy the repository lands by cannot do without."""
    return POLICY_CAPABILITIES[repository.landing_policy].requires


def _nothing(_repository: ResolvedRepository) -> frozenset[Capability]:
    return _NONE


#: One entry per action the hub admits, shipped or test.
ACTION_REQUIREMENTS: Mapping[str, Requirement] = {
    "land": _land,
    "record-findings": _nothing,
    "garden-deliver": _nothing,
    "pass": _nothing,
    "test.answer": _nothing,
    "test.sequence": _nothing,
    "test.block": _nothing,
}
