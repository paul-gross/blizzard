"""The flow driver — the one hub lane that is not a ``Sweep``, keeping every open tenant's flow lines true to where its
chunks stand. Its coroutine steps :meth:`FlowDriver.tick` through ``asyncio.to_thread`` every two seconds beside the
sweeps, gating each tenant's reconcile on one hub-wide probe and a floor (``bzh:probe-gated-pass``) and catching each
tenant on its own (``bzh:pass-isolates-tenants``). It serves one hub process, and only ``build_hosted_app`` builds it,
so no CLI or test build over a live hub's store reconciles that hub's lines."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from structlog.contextvars import bound_contextvars

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR
from blizzard.hub.domain.delivery.ports.flows import IReadFlowLinesCorpus, LineSignature
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

#: How often the driver ticks when nothing wakes it.
TICK_SECONDS = 2.0
#: The least time between two ticks, however often the driver is woken.
WAKE_FLOOR_SECONDS = 1.0

#: A tenant holding none of the rows the probe counts.
_NO_ROWS = LineSignature(marks=())

_log = get_logger("blizzard.hub.flow_driver")


@dataclass(frozen=True)
class FlowLineStores:
    """What a tick reads: the hub-wide probe, the open tenants, and each tenant's reconcile, opened through that
    tenant's own stores (``bzh:sweep-acts-in-row-tenant``)."""

    corpus: IReadFlowLinesCorpus
    tenants: Callable[[], Iterable[TenantId]]
    lines: Callable[[TenantId], FlowLineReconciler]


@domain_model
@dataclass(frozen=True)
class TickResult:
    """One tick: the tenants it reconciled in full, those whose unchanged probe it skipped, and those whose reconcile
    raised; and how many visits it submitted and submissions it closed."""

    reconciled: tuple[TenantId, ...] = ()
    skipped: tuple[TenantId, ...] = ()
    failed: tuple[TenantId, ...] = ()
    submitted: int = 0
    closed: int = 0


class FlowDriver:
    """Holds, in memory only, each tenant's arrival floor and the probe and instant of its last full reconcile, so a
    tenant is due on its own state alone. Boot starts every floor at zero."""

    def __init__(self, stores: FlowLineStores, clock: IClock, *, timer: Callable[[], float] = time.monotonic) -> None:
        self._stores = stores
        self._clock = clock
        self._timer = timer
        self._floors: dict[TenantId, int] = {}
        self._converged: dict[TenantId, tuple[LineSignature, datetime]] = {}
        self._woken = False
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Cut the driver's wait short, on the hub's event loop: the next tick runs every tenant's reconcile in full,
        no sooner than :data:`WAKE_FLOOR_SECONDS` after the last."""
        self._woken = True
        self._wake.set()

    def recover_turns(self, tenant: TenantId) -> int:
        """The boot step, run per open tenant before the hub serves: end every turn the last process left open
        ``interrupted``, then reconcile the tenant's lines in full from arrival floor zero. Both are idempotent, so a
        boot that dies midway is finished by the next. Answers how many turns it ended."""
        lines = self._stores.lines(tenant)
        ended = lines.recover_turns()
        self._floors[tenant] = lines.reconcile(0).floor
        return ended

    def tick(self, store: FlowLineStores, clock: IClock) -> TickResult:
        """Reconcile every due open tenant's lines: one probe read for the hub, then each due tenant on its own. A
        tenant that closes mid-tick is skipped for the rest of it; one whose reconcile raises is logged and left due,
        and the others still run. Only a tenant whose reconcile committed records its probe, instant, and floor."""
        forced, self._woken = self._woken, False
        tenants = sorted(store.tenants())
        self._converged = {tenant: state for tenant, state in self._converged.items() if tenant in tenants}
        signatures = store.corpus.flow_lines_signature()
        now = clock.now()
        reconciled: list[TenantId] = []
        skipped: list[TenantId] = []
        failed: list[TenantId] = []
        submitted = closed = 0
        for tenant in tenants:
            signature = signatures.get(tenant, _NO_ROWS)
            if not forced and not self._due(tenant, signature, now):
                skipped.append(tenant)
                continue
            with bound_contextvars(tenant_id=tenant):
                try:
                    result = store.lines(tenant).reconcile(self._floors.get(tenant, 0))
                except (TenantClosed, UnknownTenant):
                    _log.info("flow driver skipped a closed tenant")
                    continue
                except Exception:
                    _log.exception("flow driver reconcile failed for a tenant")
                    failed.append(tenant)
                    continue
            self._floors[tenant] = result.floor
            self._converged[tenant] = (signature, now)
            reconciled.append(tenant)
            submitted += result.submitted
            closed += result.closed
        return TickResult(tuple(reconciled), tuple(skipped), tuple(failed), submitted, closed)

    async def run(self, shutdown: asyncio.Event) -> None:
        """Tick every :data:`TICK_SECONDS`, or sooner when woken, until ``shutdown``. A tick that raises is logged and
        skipped; the next runs on time."""
        while not shutdown.is_set():
            started = self._timer()
            try:
                await asyncio.to_thread(self.tick, self._stores, self._clock)
            except Exception:
                _log.exception("flow driver tick failed")
            await self._wait(shutdown, started)

    async def _wait(self, shutdown: asyncio.Event, started: float) -> None:
        """Until shutdown, a wake, or :data:`TICK_SECONDS` after ``started``; a wake waits out
        :data:`WAKE_FLOOR_SECONDS` from ``started``."""
        await _first(shutdown, self._wake, timeout=max(0.0, started + TICK_SECONDS - self._timer()))
        if self._wake.is_set() and not shutdown.is_set():
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), timeout=max(0.0, started + WAKE_FLOOR_SECONDS - self._timer()))

    def _due(self, tenant: TenantId, signature: LineSignature, now: datetime) -> bool:
        """Never converged, its floor elapsed, or its probe moved since it last converged."""
        last = self._converged.get(tenant)
        if last is None or now - last[1] >= RECONCILE_FLOOR:
            return True
        return signature != last[0]


async def _first(*events: asyncio.Event, timeout: float) -> None:
    """Until any of ``events`` is set, or ``timeout`` seconds pass."""
    waits = [asyncio.create_task(event.wait()) for event in events]
    try:
        await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
