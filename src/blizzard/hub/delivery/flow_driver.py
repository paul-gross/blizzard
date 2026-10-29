"""The flow driver — the one hub lane that is not a ``Sweep``, taking every hub step as a turn on its flows. Its
coroutine steps :meth:`FlowDriver.tick` through ``asyncio.to_thread`` every two seconds beside the sweeps, gating each
tenant's reconcile on one hub-wide probe and a floor (``bzh:probe-gated-pass``) and catching each tenant on its own
(``bzh:pass-isolates-tenants``), then runs each turn the tick started as a task of its own. It serves one hub process,
and only ``build_hosted_app`` builds it, so no CLI or test build over a live hub's store takes that hub's turns."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from structlog.contextvars import bound_contextvars

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR, AdmittedTurn, Submission, Turn
from blizzard.hub.domain.delivery.ports.flows import IReadFlowLinesCorpus, LineSignature
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

#: How often the driver ticks when nothing wakes it.
TICK_SECONDS = 2.0
#: The least time between two ticks, however often the driver is woken.
WAKE_FLOOR_SECONDS = 1.0

# The windows between a tick's durable writes and the next (`bzh:crash-point-registry`).
_CP_FLOW_AFTER_SUBMIT_BEFORE_TURN = crashpoint(
    "flow.after-submit.before-turn", "a submission, its flow rows and its submitted facts are durable; no turn is"
)
_CP_FLOW_AFTER_TURN_START_BEFORE_PASS = crashpoint(
    "flow.after-turn-start.before-pass", "a turn and its turn-started facts are durable; its pass has not started"
)
_CP_FLOW_BOOT_AFTER_INTERRUPT = crashpoint(
    "flow.boot.after-interrupt", "one tenant's open turns are ended interrupted; the next tenant's are not"
)

#: A tenant holding none of the rows the probe counts.
_NO_ROWS = LineSignature(marks=())

_log = get_logger("blizzard.hub.flow_driver")


class ITurnRunner(Protocol):
    """Answers for one turn until it ends; cancelled, it lets the turn go without writing its end."""

    async def take(self, turn: Turn, submission: Submission) -> None: ...


class INoticeDrain(Protocol):
    """Records every notice a tenant has queued since the last drain; answers how many."""

    def drain(self, at: datetime) -> int: ...


@dataclass(frozen=True)
class FlowLineStores:
    """What a tick reads: the hub-wide probe, the open tenants, each tenant's reconcile, its turns, and its forge
    budget notices, opened through that tenant's own stores (``bzh:sweep-acts-in-row-tenant``)."""

    corpus: IReadFlowLinesCorpus
    tenants: Callable[[], Iterable[TenantId]]
    lines: Callable[[TenantId], FlowLineReconciler]
    turns: Callable[[TenantId], ITurnRunner]
    notices: Callable[[TenantId], INoticeDrain]


@domain_model
@dataclass(frozen=True)
class StartedTurn:
    """A turn a tick started, in its tenant."""

    tenant: TenantId
    turn: Turn
    submission: Submission


@domain_model
@dataclass(frozen=True)
class TickResult:
    """One tick: the tenants it reconciled in full, those whose unchanged probe it skipped, and those that raised; how
    many visits it submitted, submissions it closed, turns it ended interrupted, and forge budget notices it recorded;
    and the turns it started."""

    reconciled: tuple[TenantId, ...] = ()
    skipped: tuple[TenantId, ...] = ()
    failed: tuple[TenantId, ...] = ()
    submitted: int = 0
    closed: int = 0
    interrupted: int = 0
    started: tuple[StartedTurn, ...] = ()
    noticed: int = 0


class FlowDriver:
    """Holds, in memory only, each tenant's arrival floor and the probe and instant of its last full reconcile, and
    the instant its last tick began — written only by the one tick running at a time — and the wake mark and the turns
    a task is running, touched only on the hub's loop and handed to a tick as a snapshot. Boot starts every floor at
    zero."""

    def __init__(self, stores: FlowLineStores, clock: IClock, *, timer: Callable[[], float] = time.monotonic) -> None:
        self._stores = stores
        self._clock = clock
        self._timer = timer
        self._floors: dict[TenantId, int] = {}
        self._converged: dict[TenantId, tuple[LineSignature, datetime]] = {}
        self._last_tick: datetime | None = None
        self._woken = False
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def wake(self) -> None:
        """Cut the driver's wait short, on the hub's event loop: the next tick runs every tenant's reconcile in full,
        no sooner than :data:`WAKE_FLOOR_SECONDS` after the last."""
        self._woken = True
        self._wake.set()

    def wake_threadsafe(self) -> None:
        """:meth:`wake`, from any thread: through the driver's loop while it runs, else marking the next tick."""
        loop = self._loop
        if loop is None:
            self._woken = True
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self.wake)

    def recover_turns(self, tenant: TenantId) -> int:
        """The boot step, run per open tenant before the hub serves: end every turn the last process left open
        ``interrupted``, then reconcile the tenant's lines in full from arrival floor zero. Both are idempotent, so a
        boot that dies midway is finished by the next. Answers how many turns it ended."""
        lines = self._stores.lines(tenant)
        ended = lines.recover_turns()
        _CP_FLOW_BOOT_AFTER_INTERRUPT.reached()
        self._floors[tenant] = lines.reconcile(0).floor
        return ended

    def tick(
        self,
        store: FlowLineStores,
        clock: IClock,
        *,
        running: frozenset[str] | None = None,
        forced: bool = False,
    ) -> TickResult:
        """For every open tenant: reconcile its lines when due — every one, ``forced`` — end every open turn that
        started before the previous tick began with no task in ``running``, take the turns its visits may, and record
        its queued forge budget notices in its event log. A tenant that closes mid-tick is skipped for the rest of it;
        one that raises is logged and left due, and the others still run."""
        running = frozenset(self._running) if running is None else running
        tenants = sorted(store.tenants())
        self._converged = {tenant: state for tenant, state in self._converged.items() if tenant in tenants}
        signatures = store.corpus.flow_lines_signature()
        now = clock.now()
        swept_before, self._last_tick = self._last_tick, now
        reconciled: list[TenantId] = []
        skipped: list[TenantId] = []
        failed: list[TenantId] = []
        started: list[StartedTurn] = []
        submitted = closed = interrupted = noticed = 0
        for tenant in tenants:
            signature = signatures.get(tenant, _NO_ROWS)
            with bound_contextvars(tenant_id=tenant):
                try:
                    lines = store.lines(tenant)
                    if forced or self._due(tenant, signature, now):
                        result = lines.reconcile(self._floors.get(tenant, 0))
                        self._floors[tenant] = result.floor
                        self._converged[tenant] = (signature, now)
                        reconciled.append(tenant)
                        submitted += result.submitted
                        closed += result.closed
                        if result.submitted:
                            _CP_FLOW_AFTER_SUBMIT_BEFORE_TURN.reached()
                    else:
                        skipped.append(tenant)
                    if swept_before is not None:
                        interrupted += lines.end_unanswered(started_before=swept_before, running=running)
                    admitted = lines.admit(self._floors.get(tenant, 0), now)
                    noticed += store.notices(tenant).drain(now)
                except (TenantClosed, UnknownTenant):
                    _log.info("flow driver skipped a closed tenant")
                    continue
                except Exception:
                    _log.exception("flow driver tick failed for a tenant")
                    failed.append(tenant)
                    continue
            if admitted:
                _CP_FLOW_AFTER_TURN_START_BEFORE_PASS.reached()
            started.extend(_started(tenant, admitted))
        return TickResult(
            tuple(reconciled), tuple(skipped), tuple(failed), submitted, closed, interrupted, tuple(started), noticed
        )

    async def step(self) -> TickResult:
        """One tick, then every turn it started run to its end — how a test drives the driver."""
        result = await asyncio.to_thread(
            self.tick, self._stores, self._clock, running=frozenset(self._running), forced=self._take_wake()
        )
        tasks = self._spawn(result.started)
        if tasks:
            await asyncio.wait(tasks)
        return result

    async def run(self, shutdown: asyncio.Event) -> None:
        """Tick every :data:`TICK_SECONDS`, or sooner when woken, until ``shutdown``, starting a task per turn each
        tick takes. A tick that raises is logged and skipped. On shutdown every turn's task is cancelled and awaited,
        writing nothing: the next boot ends those turns."""
        self._loop = asyncio.get_running_loop()
        try:
            while not shutdown.is_set():
                started = self._timer()
                try:
                    result = await asyncio.to_thread(
                        self.tick,
                        self._stores,
                        self._clock,
                        running=frozenset(self._running),
                        forced=self._take_wake(),
                    )
                    self._spawn(result.started)
                except Exception:
                    _log.exception("flow driver tick failed")
                await self._wait(shutdown, started)
        finally:
            self._loop = None
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks)

    def _take_wake(self) -> bool:
        """Whether the driver was woken since the last tick, clearing the mark — on the loop, so no wake is lost."""
        woken, self._woken = self._woken, False
        return woken

    def _spawn(self, started: Iterable[StartedTurn]) -> list[asyncio.Task[None]]:
        tasks = [asyncio.create_task(self._turn(turn)) for turn in started]
        for task in tasks:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return tasks

    async def _turn(self, started: StartedTurn) -> None:
        """A turn's task: registered as running as its first act, unregistered by its one ``finally``. A failure ends
        only its own turn."""
        turn_id = started.turn.turn_id
        self._running.add(turn_id)
        try:
            with bound_contextvars(tenant_id=started.tenant, turn_id=turn_id):
                await self._stores.turns(started.tenant).take(started.turn, started.submission)
        except Exception:
            _log.exception("flow driver turn failed", turn_id=turn_id)
        finally:
            self._running.discard(turn_id)
            self.wake()

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


class StepWake:
    """``IHubStepWake`` for every tenant's services: wakes the driver it is bound to, and nothing while none is —
    every app but the hosted hub's."""

    def __init__(self) -> None:
        self._wake: Callable[[], None] | None = None

    def bind(self, driver: FlowDriver) -> None:
        self._wake = driver.wake_threadsafe

    def wake(self) -> None:
        if self._wake is not None:
            self._wake()


def _started(tenant: TenantId, admitted: Iterable[AdmittedTurn]) -> list[StartedTurn]:
    return [StartedTurn(tenant, a.turn, a.submission) for a in admitted]


async def _first(*events: asyncio.Event, timeout: float) -> None:
    """Until any of ``events`` is set, or ``timeout`` seconds pass."""
    waits = [asyncio.create_task(event.wait()) for event in events]
    try:
        await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
