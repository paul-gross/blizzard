"""Orphaning a lease's session: wherever the runner kills a lease's worker, or finds it gone without a result, it hands
that session to its harness owner to stop a turn that can outlive its worker. The runner chooses the session — always
the lease's own, never one under takeover — and nothing waits on the owner beyond its own bound."""

from __future__ import annotations

from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository

_log = get_logger("blizzard.runner.lifecycle")


class OrphanStores(Protocol):
    @property
    def takeover(self) -> IReadTakeoverRepository: ...


class OrphanContext(Protocol):
    @property
    def harnesses(self) -> IHarnessRegistry: ...
    @property
    def stores(self) -> OrphanStores: ...


def orphan_resultless_exit(ctx: OrphanContext, lease: Lease, output: str, *, via: str) -> None:
    """Orphan ``lease``'s session when its exited worker's captured ``output`` carries no usable result; never
    raises. An owner that cannot be resolved is left to the caller's own resolution to report."""
    session = lease.session
    if session is None:
        return
    try:
        usable = ctx.harnesses.lifecycle_and_verdict(session.harness_id).has_usable_output(output)
    except (UnknownHarnessError, UnavailableHarnessError):
        return
    if not usable:
        orphan_session(ctx, lease, via=via)


def orphan_session(ctx: OrphanContext, lease: Lease, *, via: str) -> None:
    """Hand ``lease``'s session to its owner's ``orphan``; never raises. A lease with no session, a session whose
    owner cannot be resolved, or one an open takeover holds is left alone."""
    session = lease.session
    if session is None:
        return
    try:
        takeover = ctx.stores.takeover.open_takeover_for_chunk(lease.chunk_id)
        if takeover is not None and takeover.session_id == session.session_id:
            return
        ctx.harnesses.lifecycle(session.harness_id).orphan(session.session_id)
    except (UnknownHarnessError, UnavailableHarnessError) as exc:
        _log.warning(
            "orphaned session's owner unavailable — left to reconcile",
            via=via,
            lease_id=lease.lease_id,
            harness_id=session.harness_id,
            detail=str(exc),
        )
    except Exception:
        _log.exception("orphaning a killed worker's session failed", via=via, lease_id=lease.lease_id)


__all__ = ["OrphanContext", "orphan_resultless_exit", "orphan_session"]
