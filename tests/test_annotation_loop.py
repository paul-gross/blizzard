"""Unit tests for the hub's sweep-loop background driver (``Sweep``), shared by the
forge-status annotation loop (issue #179) and the close-intent drain loop (blizzard#383),
and for ``_lifespan``'s task-starting conditions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.hub.app import Sweep, _lifespan
from blizzard.hub.config import HubConfig

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
        asyncio.gather(Sweep(reconciler, 0, shutdown, "test").run(), _stop_once_three_sweeps_land()),
        timeout=2.0,
    )

    assert reconciler.calls >= 3


async def test_loop_survives_a_sweep_that_raises() -> None:
    """A bad tick is logged and swallowed — it must not kill the loop."""
    reconciler = _CountingReconciler(fail_on={1})
    shutdown = asyncio.Event()

    async def _stop_once_two_sweeps_land() -> None:
        while reconciler.calls < 2:
            await asyncio.sleep(0)
        shutdown.set()

    await asyncio.wait_for(
        asyncio.gather(Sweep(reconciler, 0, shutdown, "test").run(), _stop_once_two_sweeps_land()),
        timeout=2.0,
    )

    assert reconciler.calls >= 2


async def test_loop_returns_promptly_when_shutdown_fires_mid_wait() -> None:
    """A long interval, but the loop returns almost immediately once ``shutdown`` fires
    concurrently — the wait races the event rather than holding a drain (issue #47)."""
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(Sweep(reconciler, 3600, shutdown, "test").run())
    await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait
    assert reconciler.calls == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)


# --------------------------------------------------------------------------- #
# _lifespan's task-starting conditions


class _FakeWorkSources:
    """A minimal stand-in for ``IWorkSourceRegistry`` — only ``annotating_names()``,
    the one method ``Sweep.all`` still consults to decide whether to start a loop."""

    def __init__(self, *, annotating: tuple[str, ...] = ()) -> None:
        self._annotating = annotating

    def annotating_names(self) -> list[str]:
        return list(self._annotating)


class _FakeServices:
    """A minimal stand-in for ``HubServices`` — only the attributes ``_lifespan``
    reads: ``work_sources`` (the forge-status start-condition), ``close_drain``
    (blizzard#383 — started unconditionally, no source gate), ``event_derivation``
    (blizzard#254 — started unconditionally too), and ``work_item_materialization``
    (blizzard#366 D9 — the same)."""

    def __init__(
        self,
        *,
        work_sources: _FakeWorkSources,
        close_drain: _CountingReconciler | None = None,
        event_derivation: _CountingReconciler | None = None,
        work_item_materialization: _CountingReconciler | None = None,
    ) -> None:
        self.work_sources = work_sources
        self.close_drain = close_drain or _CountingReconciler()
        self.event_derivation = event_derivation or _CountingReconciler()
        self.work_item_materialization = work_item_materialization or _CountingReconciler()
        self.chunks = None  # unread unless annotating_names() is non-empty, which these tests never set


class _FakeState:
    def __init__(self, services: _FakeServices, config: HubConfig) -> None:
        self.services = services
        self.config = config
        self.shutdown = asyncio.Event()


class _FakeApp:
    """Duck-types the two attributes ``_lifespan`` reads off a real ``FastAPI``
    instance — ``app.state.{services,config,shutdown}`` — with no ASGI machinery."""

    def __init__(self, services: _FakeServices, config: HubConfig) -> None:
        self.state = _FakeState(services, config)


async def test_lifespan_starts_the_event_derivation_loop_unconditionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """blizzard#254 D1: no work source opts a chunk's transcript events into anything —
    the sweep is yielded and started regardless."""
    monkeypatch.setattr("blizzard.hub.app.random.uniform", lambda _lo, _hi: 0.0)  # no jitter to wait out
    event_derivation = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources())
    services.event_derivation = event_derivation
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    async with _lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait

    assert event_derivation.calls == 1


async def test_lifespan_starts_the_work_item_materialization_loop_unconditionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """blizzard#366 D9: materialization is idempotent inside one transaction against one
    store, so there is nothing a work-source opt-in would protect — the sweep is yielded
    and started regardless, the same ground ``event_derivation`` stands on."""
    monkeypatch.setattr("blizzard.hub.app.random.uniform", lambda _lo, _hi: 0.0)  # no jitter to wait out
    materialization = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources())
    services.work_item_materialization = materialization
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    async with _lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait

    assert materialization.calls == 1


async def test_lifespan_starts_the_close_drain_loop_unconditionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """blizzard#383 D3: the enqueue is source-agnostic, so the drain runs whether or not
    any source is close-capable today — the sweep is yielded and started regardless,
    like its two siblings above."""
    monkeypatch.setattr("blizzard.hub.app.random.uniform", lambda _lo, _hi: 0.0)  # no jitter to wait out
    close_drain = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources())
    services.close_drain = close_drain
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    async with _lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait

    assert close_drain.calls == 1


# --------------------------------------------------------------------------- #
# initial delay, elapsed-time logging, and overrun warning (blizzard#524 D8)


async def test_initial_delay_is_honored_before_the_first_sweep() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(Sweep(reconciler, 3600, shutdown, "test", initial_delay_seconds=3600).run())
    await asyncio.sleep(0.05)
    assert reconciler.calls == 0  # still waiting out the initial delay

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert reconciler.calls == 0  # shutdown fired before the delay ever elapsed


async def test_shutdown_during_the_initial_delay_returns_promptly() -> None:
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(Sweep(reconciler, 3600, shutdown, "test", initial_delay_seconds=3600).run())
    await asyncio.sleep(0.05)
    shutdown.set()

    await asyncio.wait_for(task, timeout=1.0)  # returns almost immediately, not after the 3600s delay


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
                Sweep(reconciler, 0, shutdown, "test", timer=lambda: next(ticks)).run(),
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
                Sweep(reconciler, 1, shutdown, "sweep-under-test", timer=lambda: next(ticks)).run(),
                _stop_once_one_sweep_lands(),
            ),
            timeout=2.0,
        )

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "sweep pass exceeded its own interval"
    assert warnings[0]["sweep"] == "sweep-under-test"


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
                Sweep(reconciler, 60, shutdown, "test", timer=lambda: next(ticks)).run(),
                _stop_once_one_sweep_lands(),
            ),
            timeout=2.0,
        )

    assert not [entry for entry in logs if entry["log_level"] == "warning"]
