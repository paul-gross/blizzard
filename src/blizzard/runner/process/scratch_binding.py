"""The rule binding a host's effective scratch root.

A root may change only once every lease on the host has drained, and the previous
root is reconciled before the replacement is published, so a crash can only cause
that reconciliation to be repeated. The caller holds the host lock. This module does
no file I/O: the binding file and the root checks sit behind
:class:`ScratchBindingPort`, implemented in
``src/blizzard/runner/process/internal/scratch_binding_files.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.process.scratch_cleanup import ClosedLeaseLookup, sweep_closed_legacy


class ScratchSweeper(Protocol):
    def sweep_orphans(self, active_lease_ids: set[str], *, strict: bool = False) -> int: ...

    def sweep_legacy(self, removable: Callable[[str], bool], *, strict: bool = False) -> int: ...


class ScratchSweeperFactory(Protocol):
    def __call__(self, root: str, *, runner_id: str, legacy_root: str) -> ScratchSweeper: ...


class ScratchBindingPort(Protocol):
    def prepare(self, root: Path) -> None:
        """Create ``root`` owner-only, prove it usable, and make its path durable."""
        ...

    def probe(self, root: Path) -> None:
        """Raise unless the existing ``root`` is usable."""
        ...

    def read_previous(self) -> Path | None:
        """The published root, or ``None`` when none was ever published."""
        ...

    def publish(self, root: Path) -> None:
        """Durably replace the published root."""
        ...


class BindingAction(Enum):
    UNCHANGED = "unchanged"
    PUBLISH = "publish"
    RECONCILE_PREVIOUS_THEN_PUBLISH = "reconcile-previous-then-publish"
    REFUSE_INVALID_PREVIOUS = "refuse-invalid-previous"
    REFUSE_ACTIVE_LEASES = "refuse-active-leases"


def decide_binding(previous: Path | None, root: Path, *, active: bool) -> BindingAction:
    """What binding ``root`` over ``previous`` calls for; ``active`` is any lease live on the host."""
    if previous is None:
        return BindingAction.PUBLISH
    if not previous.is_absolute() or "\x00" in str(previous):
        return BindingAction.REFUSE_INVALID_PREVIOUS
    if previous == root:
        return BindingAction.UNCHANGED
    if active:
        return BindingAction.REFUSE_ACTIVE_LEASES
    return BindingAction.RECONCILE_PREVIOUS_THEN_PUBLISH


@domain_model
@dataclass(frozen=True)
class RunnerScratchLeases:
    """One runner's lease facts: the ids still active, and the closure lookup."""

    active_lease_ids: Collection[str]
    closed: ClosedLeaseLookup


def bind_scratch_root(
    port: ScratchBindingPort,
    root: Path,
    legacy_root: Path,
    runners: Mapping[str, RunnerScratchLeases],
    scratch_factory: ScratchSweeperFactory,
) -> None:
    """Bind and reconcile ``root`` once, before any runner on this host can spawn.

    ``root`` is already resolved. A previous root is reconciled before the binding
    is switched; if preparing or reconciling fails, nothing is published.
    """

    def reconcile(target: Path) -> None:
        port.probe(target)
        for runner_id, leases in runners.items():
            scratch = scratch_factory(str(target), runner_id=runner_id, legacy_root=str(legacy_root))
            scratch.sweep_orphans(set(leases.active_lease_ids), strict=True)
            sweep_closed_legacy(scratch, leases.closed, strict=True)

    port.prepare(root)
    previous = port.read_previous()
    action = decide_binding(previous, root, active=any(leases.active_lease_ids for leases in runners.values()))
    if action is BindingAction.REFUSE_INVALID_PREVIOUS:
        raise RuntimeError(f"Invalid scratch root binding: {previous}")
    if action is BindingAction.REFUSE_ACTIVE_LEASES:
        raise RuntimeError(
            "Worker scratch root changed while leases are active; restore the old root and drain leases "
            "before editing the root"
        )
    if action is not BindingAction.UNCHANGED:
        if action is BindingAction.RECONCILE_PREVIOUS_THEN_PUBLISH and previous is not None:
            reconcile(previous)
        port.publish(root)
    reconcile(root)
