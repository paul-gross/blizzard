"""Reconcile legacy scratch against runner-scoped closure facts."""

from collections.abc import Callable
from typing import Protocol

from blizzard.runner.leases.record import IReadLeaseRecordRepository


class LegacySweeper(Protocol):
    def sweep_legacy(self, removable: Callable[[str], bool], *, strict: bool = False) -> int: ...


def sweep_closed_legacy(scratch: LegacySweeper, repo: IReadLeaseRecordRepository, *, strict: bool = False) -> int:
    """Collect candidates before deleting, then remove only proven closed payloads."""
    candidates: set[str] = set()

    def collect(lease_id: str) -> bool:
        candidates.add(lease_id)
        return False

    scratch.sweep_legacy(collect, strict=strict)
    closed = repo.closed_lease_ids(candidates)
    return scratch.sweep_legacy(closed.__contains__, strict=strict)
