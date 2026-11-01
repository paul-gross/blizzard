"""Unit tests for the hub's sweep-loop background driver (``Sweep``), shared by every timed sweep,
and for ``lifespan``'s task-starting conditions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub.app import Sweep, lifespan
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.sweeps.locks import SweepLocks
from blizzard.hub.domain.sweeps.names import SweepMode, SweepModes, SweepName
from blizzard.hub.domain.sweeps.roster import ISweepsEveryTenant, SweepRoster

pytestmark = pytest.mark.unit


class _CountingReconciler:
    """A fake reconciler — counts ``sweep()`` calls, optionally raising on named ones."""

    def __init__(self, *, fail_on: set[int] | None = None) -> None:
        self.calls = 0
        self._fail_on = fail_on or set()

    def sweep(self) -> None:
        self.calls += 1
        if self.calls in self._fail_on:
            raise RuntimeError("boom")


async def test_loop_calls_sweep_once_per_interval_with_no_real_sleeping() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    async def _stop_once_three_sweeps_land() -> None:
        while reconciler.calls < 3:
            await asyncio.sleep(0)
        shutdown.set()

    await asyncio.wait_for(
        asyncio.gather(
            Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 0, shutdown).run(), _stop_once_three_sweeps_land()
        ),
        timeout=2.0,
    )

    assert reconciler.calls >= 3


async def test_loop_survives_a_sweep_that_raises() -> None:
    """A bad tick is logged and swallowed by the roster's pass — it must not kill the loop."""
    reconciler = _CountingReconciler(fail_on={1})
    shutdown = asyncio.Event()
    roster = _roster({SweepName.CLOSE_DRAIN: reconciler})

    async def _stop_once_two_sweeps_land() -> None:
        while reconciler.calls < 2:
            await asyncio.sleep(0)
        shutdown.set()

    await asyncio.wait_for(
        asyncio.gather(
            Sweep(SweepName.CLOSE_DRAIN, lambda: roster.pass_every_tenant(SweepName.CLOSE_DRAIN), 0, shutdown).run(),
            _stop_once_two_sweeps_land(),
        ),
        timeout=2.0,
    )

    assert reconciler.calls >= 2


async def test_loop_returns_promptly_when_shutdown_fires_mid_wait() -> None:
    """A long interval, but the loop returns almost immediately once ``shutdown`` fires
    concurrently — the wait races the event rather than holding a drain."""
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 3600, shutdown).run())
    await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait
    assert reconciler.calls == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)


# --------------------------------------------------------------------------- #
# lifespan's task-starting conditions


def _roster(every_tenant: dict[SweepName, ISweepsEveryTenant]) -> SweepRoster:
    return SweepRoster(
        every_tenant=every_tenant, one_tenant={}, locks=SweepLocks(), clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    )


class _FakeWorkSources:
    """A minimal stand-in for ``IWorkSourceRegistry`` — only ``annotating_ids()``,
    the one method ``Sweep.all`` still consults to decide whether to start a loop."""

    def __init__(self, *, annotating: tuple[str, ...] = ()) -> None:
        self._annotating = annotating

    def annotating_ids(self) -> list[str]:
        return list(self._annotating)


class _FakeServices:
    """A minimal stand-in for ``HubServices`` — only the attributes ``lifespan``
    reads: ``sweeps`` (the roster over ``annotation``, when given, ``close_drain``, ``event_derivation`` and
    ``tenant_teardown_sweep``, with no trace or egress export), ``action_loop`` (started and stopped around the
    sweeps), and ``close_tenant_objects`` (run before the loop stops)."""

    def __init__(
        self,
        *,
        work_sources: _FakeWorkSources,
        close_drain: _CountingReconciler | None = None,
        event_derivation: _CountingReconciler | None = None,
        annotation: _CountingReconciler | None = None,
    ) -> None:
        self.work_sources = work_sources
        self.close_drain = close_drain or _CountingReconciler()
        self.event_derivation = event_derivation or _CountingReconciler()
        self.tenant_teardown_sweep = _CountingReconciler()
        self.annotation = annotation
        self.action_loop = ActionLoop()
        self.close_tenant_objects = lambda: None

    @property
    def sweeps(self) -> SweepRoster:
        built: dict[SweepName, ISweepsEveryTenant] = {
            SweepName.EVENT_DERIVATION: self.event_derivation,
            SweepName.CLOSE_DRAIN: self.close_drain,
            SweepName.TENANT_TEARDOWN: self.tenant_teardown_sweep,
        }
        if self.annotation is not None:
            built[SweepName.FORGE_STATUS] = self.annotation
        return _roster(built)


class _FakeState:
    def __init__(self, services: _FakeServices, config: HubConfig) -> None:
        self.services = services
        self.config = config
        self.shutdown = asyncio.Event()
        self.platform_tracing = DisabledPlatformTracing()
        self.flow_driver = None


class _FakeApp:
    """Duck-types the two attributes ``lifespan`` reads off a real ``FastAPI``
    instance — ``app.state.{services,config,shutdown,platform_tracing,flow_driver}`` — with no ASGI machinery."""

    def __init__(self, services: _FakeServices, config: HubConfig) -> None:
        self.state = _FakeState(services, config)


@pytest.fixture
def no_early_second_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin each lifespan sweep's jitter to its whole interval. The jitter is drawn from ``[0, interval)`` and offsets
    the second pass, so a draw inside a test's window would add a pass to a count that expects only the first."""
    monkeypatch.setattr("blizzard.hub.app.random.uniform", lambda low, high: high)


@pytest.mark.usefixtures("no_early_second_pass")
async def test_lifespan_starts_the_event_derivation_loop_unconditionally(tmp_path: Path) -> None:
    """No work source opts a chunk's transcript events into anything —
    the sweep is yielded and started regardless."""
    event_derivation = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources())
    services.event_derivation = event_derivation
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    async with lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait

    assert event_derivation.calls == 1


@pytest.mark.usefixtures("no_early_second_pass")
async def test_lifespan_starts_the_close_drain_loop_unconditionally(tmp_path: Path) -> None:
    """The enqueue is source-agnostic, so the drain runs whether or not
    any source is close-capable today — the sweep is yielded and started regardless,
    like its sibling above."""
    close_drain = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources())
    services.close_drain = close_drain
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    async with lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait

    assert close_drain.calls == 1


async def test_the_sweep_set_carries_the_injected_annotator_when_one_is_present(tmp_path: Path) -> None:
    annotator = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources(annotating=("widget",)), annotation=annotator)
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    sweeps = list(Sweep.all(app))  # type: ignore[arg-type]

    [sweep] = [sweep for sweep in sweeps if sweep.logger_name == "blizzard.hub.forge_status"]
    sweep.reconciler()
    assert annotator.calls == 1
    assert sweep.interval_seconds == app.state.config.annotation_interval_seconds
    assert sweep.shutdown is app.state.shutdown
    assert sweep.jitter_seconds is not None
    assert 0 <= sweep.jitter_seconds < sweep.interval_seconds


async def test_the_sweep_set_has_no_forge_status_sweep_without_an_annotator(tmp_path: Path) -> None:
    services = _FakeServices(work_sources=_FakeWorkSources())
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    sweeps = list(Sweep.all(app))  # type: ignore[arg-type]

    assert all(sweep.logger_name != "blizzard.hub.forge_status" for sweep in sweeps)


# --------------------------------------------------------------------------- #
# jitter, elapsed-time logging, and overrun warning


async def test_the_first_pass_runs_immediately_even_with_a_large_jitter() -> None:
    """A freshly booted or crash-recovered process must not wait out a sweep's own
    jitter before its first, convergence-critical pass — only its
    second pass onward is offset."""
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(
        Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 3600, shutdown, jitter_seconds=3600).run()
    )
    await asyncio.sleep(0.05)
    assert reconciler.calls == 1  # the first pass never waits out the jitter

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert reconciler.calls == 1  # shutdown fired before the post-first-pass jitter elapsed


async def test_shutdown_during_the_post_first_pass_jitter_returns_promptly() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(
        Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 3600, shutdown, jitter_seconds=3600).run()
    )
    await asyncio.sleep(0.05)  # let the immediate first pass land
    shutdown.set()

    await asyncio.wait_for(task, timeout=1.0)  # returns almost immediately, not after the 3600s jitter


async def test_elapsed_time_is_logged_every_pass() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()
    ticks = iter([100.0, 100.25, 200.0, 200.1])  # (start, end) pairs for two passes

    async def _stop_once_two_sweeps_land() -> None:
        while reconciler.calls < 2:
            await asyncio.sleep(0)
        shutdown.set()

    with capture_logs() as logs:
        await asyncio.wait_for(
            asyncio.gather(
                Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 0, shutdown, timer=lambda: next(ticks)).run(),
                _stop_once_two_sweeps_land(),
            ),
            timeout=2.0,
        )

    completed = [entry for entry in logs if entry["event"] == "sweep pass completed"]
    assert len(completed) >= 2
    assert all("elapsed_seconds" in entry for entry in completed)


async def test_a_pass_exceeding_its_interval_logs_a_warning_naming_the_sweep() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()
    ticks = iter([0.0, 10.0])  # a single 10s pass against a 1s interval — an overrun

    async def _stop_once_one_sweep_lands() -> None:
        while reconciler.calls < 1:
            await asyncio.sleep(0)
        shutdown.set()

    with capture_logs() as logs:
        await asyncio.wait_for(
            asyncio.gather(
                Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 1, shutdown, timer=lambda: next(ticks)).run(),
                _stop_once_one_sweep_lands(),
            ),
            timeout=2.0,
        )

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "sweep pass exceeded its own interval"
    assert warnings[0]["sweep"] == "blizzard.hub.work_closure"


async def test_a_pass_within_its_interval_logs_no_overrun_warning() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()
    ticks = iter([0.0, 0.1])  # well inside a 60s interval

    async def _stop_once_one_sweep_lands() -> None:
        while reconciler.calls < 1:
            await asyncio.sleep(0)
        shutdown.set()

    with capture_logs() as logs:
        await asyncio.wait_for(
            asyncio.gather(
                Sweep(SweepName.CLOSE_DRAIN, reconciler.sweep, 60, shutdown, timer=lambda: next(ticks)).run(),
                _stop_once_one_sweep_lands(),
            ),
            timeout=2.0,
        )

    assert not [entry for entry in logs if entry["log_level"] == "warning"]


# --------------------------------------------------------------------------- #
# modes and locks


async def test_the_sweep_set_has_no_sweep_for_a_stepped_one(tmp_path: Path) -> None:
    services = _FakeServices(work_sources=_FakeWorkSources())
    config = HubConfig(
        root=tmp_path,
        db_url="sqlite:///:memory:",
        sweeps=SweepModes({SweepName.CLOSE_DRAIN: SweepMode.STEPPED, SweepName.EVENT_DERIVATION: SweepMode.TIMED}),
    )

    names = [sweep.config_name for sweep in Sweep.all(_FakeApp(services, config))]  # type: ignore[arg-type]

    assert names == [SweepName.EVENT_DERIVATION, SweepName.TENANT_TEARDOWN]


@pytest.mark.usefixtures("no_early_second_pass")
async def test_a_stepped_sweep_runs_no_pass_at_boot_and_a_timed_one_runs_as_today(tmp_path: Path) -> None:
    close_drain, event_derivation = _CountingReconciler(), _CountingReconciler()
    services = _FakeServices(
        work_sources=_FakeWorkSources(), close_drain=close_drain, event_derivation=event_derivation
    )
    config = HubConfig(
        root=tmp_path, db_url="sqlite:///:memory:", sweeps=SweepModes({SweepName.CLOSE_DRAIN: SweepMode.STEPPED})
    )

    async with lifespan(_FakeApp(services, config)):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)

    assert close_drain.calls == 0
    assert event_derivation.calls == 1


async def test_a_timed_pass_that_meets_a_held_lock_skips_its_interval() -> None:
    reconciler = _CountingReconciler()
    locks = SweepLocks()
    roster = SweepRoster(
        every_tenant={SweepName.CLOSE_DRAIN: reconciler},
        one_tenant={},
        locks=locks,
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    shutdown = asyncio.Event()

    with capture_logs() as logs, locks.hold(SweepName.CLOSE_DRAIN):
        task = asyncio.ensure_future(
            Sweep(SweepName.CLOSE_DRAIN, lambda: roster.pass_every_tenant(SweepName.CLOSE_DRAIN), 3600, shutdown).run()
        )
        await asyncio.sleep(0.05)
        shutdown.set()
        await asyncio.wait_for(task, timeout=1.0)

    assert reconciler.calls == 0
    assert [(entry["event"], entry.get("reason")) for entry in logs] == [("sweep pass skipped", "in progress")]
