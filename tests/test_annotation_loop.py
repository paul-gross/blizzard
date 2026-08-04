"""Unit tests for the hub's sweep-loop background driver (``_run_sweep_loop``), shared
by the forge-status annotation loop (issue #179) and the delivery closure loop (issue
#216), and for ``_lifespan``'s closure-task-starting gate (issue #216).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from blizzard.hub.app import _lifespan, _run_sweep_loop
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
        asyncio.gather(_run_sweep_loop(reconciler, 0, shutdown, logger_name="test"), _stop_once_three_sweeps_land()),
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
        asyncio.gather(_run_sweep_loop(reconciler, 0, shutdown, logger_name="test"), _stop_once_two_sweeps_land()),
        timeout=2.0,
    )

    assert reconciler.calls >= 2


async def test_loop_returns_promptly_when_shutdown_fires_mid_wait() -> None:
    """A long interval, but the loop still returns almost immediately once
    ``shutdown`` fires concurrently — proving the wait races the event rather
    than holding a graceful drain for up to the interval (issue #47's own
    stream-shutdown race, restated for this loop)."""
    reconciler = _CountingReconciler()
    shutdown = asyncio.Event()

    task = asyncio.ensure_future(_run_sweep_loop(reconciler, 3600, shutdown, logger_name="test"))
    await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait
    assert reconciler.calls == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)


# --------------------------------------------------------------------------- #
# _lifespan's closure-task-starting condition (issue #216)
# --------------------------------------------------------------------------- #


class _FakeWorkSources:
    """A minimal stand-in for ``IWorkSourceRegistry`` — only the two ``*_names()``
    methods ``_lifespan`` consults to decide whether to start each loop."""

    def __init__(self, *, annotating: tuple[str, ...] = (), closing: tuple[str, ...] = ()) -> None:
        self._annotating = annotating
        self._closing = closing

    def annotating_names(self) -> list[str]:
        return list(self._annotating)

    def closing_names(self) -> list[str]:
        return list(self._closing)


class _FakeServices:
    """A minimal stand-in for ``HubServices`` — only the attributes ``_lifespan``
    reads: ``work_sources`` (the start-condition) and ``delivery_closure`` (the
    already-built reconciler it starts or not, mirroring the composition root)."""

    def __init__(self, *, work_sources: _FakeWorkSources, delivery_closure: _CountingReconciler) -> None:
        self.work_sources = work_sources
        self.delivery_closure = delivery_closure
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


async def test_lifespan_does_not_start_the_closure_loop_when_no_source_opts_in(tmp_path: Path) -> None:
    closure = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources(), delivery_closure=closure)
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:"))

    async with _lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0)

    assert closure.calls == 0


async def test_lifespan_starts_the_closure_loop_when_a_source_opts_in(tmp_path: Path) -> None:
    closure = _CountingReconciler()
    services = _FakeServices(work_sources=_FakeWorkSources(closing=("default",)), delivery_closure=closure)
    app = _FakeApp(services, HubConfig(root=tmp_path, db_url="sqlite:///:memory:", annotation_interval_seconds=3600))

    async with _lifespan(app):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait

    assert closure.calls == 1
