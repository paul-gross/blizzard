"""What each hub action needs its repositories' forges to offer, keyed by action name.

Each entry is a function of the resolved repository, so a requirement that varies by repository
changes only what an entry answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.repositories import ResolvedRepository

Requirement = Callable[[ResolvedRepository], frozenset[Capability]]

_LAND = frozenset(
    {
        Capability.REFS_READ,
        Capability.COMPARE,
        Capability.CHANGES_OPEN,
        Capability.CHANGES_READ,
        Capability.CHANGES_UPDATE_FROM_BASE,
        Capability.CHANGES_MERGE_MERGE,
        Capability.CHECKS_READ,
    }
)

_NONE: frozenset[Capability] = frozenset()


def _land(_repository: ResolvedRepository) -> frozenset[Capability]:
    return _LAND


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
}
