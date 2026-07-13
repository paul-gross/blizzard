"""The hub's readiness rule (``bzh:domain-core``) — minimal, dependency-free.

Readiness is the one operational rule the hub daemon owns at the domain layer for
now: the hub is *ready* when its store is reachable **and** migrated to exactly
the revision this build expects (``bzh:manual-migrations`` — a skew fails loud, it
never serves on a mismatch). The rule imports only the store-status value type and
the standard library; the store engine sits behind the injected reader seam, so
this evaluates in a unit test with a fake reader and no database.

The fleet business rules (chunk transitions, delivery, ask/answer) land in this
layer in P6; this is the seam and the composition pattern they slot into.
"""

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


def evaluate_readiness(status: StoreStatus, *, expected_revision: str | None) -> Readiness:
    """Apply the readiness rule to a store reading and the build's expected head."""
    if not status.reachable:
        return Readiness(
            ready=False,
            store_reachable=False,
            store_revision=None,
            expected_revision=expected_revision,
            detail=status.detail or "store unreachable",
        )
    at_head = status.revision == expected_revision
    detail = "" if at_head else f"store at {status.revision or '(unmigrated)'}, expected {expected_revision}"
    return Readiness(
        ready=at_head,
        store_reachable=True,
        store_revision=status.revision,
        expected_revision=expected_revision,
        detail=detail,
    )


class ReadinessService:
    """Composition-root-wired readiness evaluator: a read seam + the expected head."""

    def __init__(self, *, reader: IStoreStatusReader, expected_revision: str | None) -> None:
        self._reader = reader
        self._expected_revision = expected_revision

    def evaluate(self) -> Readiness:
        return evaluate_readiness(self._reader.read_status(), expected_revision=self._expected_revision)
