"""Reconcile legacy scratch against runner-scoped closure facts."""

from collections.abc import Callable, Collection
from typing import Protocol


class LegacySweeper(Protocol):
    def sweep_legacy(self, removable: Callable[[str], bool], *, strict: bool = False) -> int: ...


class ClosedLeaseLookup(Protocol):
    def closed_lease_ids(self, lease_ids: Collection[str]) -> set[str]: ...


def sweep_closed_legacy(scratch: LegacySweeper, repo: ClosedLeaseLookup, *, strict: bool = False) -> int:
    """Collect candidates before deleting, then remove only proven closed payloads."""
    candidates: set[str] = set()

    def collect(lease_id: str) -> bool:
        candidates.add(lease_id)
        return False

    scratch.sweep_legacy(collect, strict=strict)
    closed = repo.closed_lease_ids(candidates)
    return scratch.sweep_legacy(closed.__contains__, strict=strict)
