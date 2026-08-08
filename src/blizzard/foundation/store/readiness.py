"""A daemon's readiness rule (``bzh:domain-core``) — one owner for the hub and the runner.

A daemon is *ready* when its store is reachable **and** migrated to exactly the revision this
build expects (``bzh:manual-migrations`` — a skew fails loud, it never serves on a mismatch)."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.store.status import IStoreStatusReader, StoreStatus


@dataclass(frozen=True)
class Readiness:
    """The evaluated readiness of a daemon — derived, never stored (``bzh:facts-not-status``)."""

    ready: bool
    store_reachable: bool
    store_revision: str | None
    expected_revision: str | None
    detail: str = ""

    @classmethod
    def of(cls, status: StoreStatus, *, expected_revision: str | None) -> Readiness:
        if not status.reachable:
            return cls(
                ready=False,
                store_reachable=False,
                store_revision=None,
                expected_revision=expected_revision,
                detail=status.detail or "store unreachable",
            )
        at_head = status.revision == expected_revision
        detail = "" if at_head else f"store at {status.revision or '(unmigrated)'}, expected {expected_revision}"
        return cls(
            ready=at_head,
            store_reachable=True,
            store_revision=status.revision,
            expected_revision=expected_revision,
            detail=detail,
        )


@dataclass(frozen=True)
class ReadinessService:
    """Composition-root-wired readiness evaluator: a read seam + the expected head."""

    reader: IStoreStatusReader
    expected_revision: str | None

    def evaluate(self) -> Readiness:
        return Readiness.of(self.reader.read_status(), expected_revision=self.expected_revision)
