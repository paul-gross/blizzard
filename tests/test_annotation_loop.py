"""The forge-status background driver — a thin sleep-and-call wrapper (issue #179, Phase 5).

``_run_annotation_loop`` is unit-tested by direct import, mirroring
``tests/test_events_stream.py``'s own pattern for ``_stream``: a counting fake
reconciler stands in for :class:`~blizzard.hub.domain.forge_status.AnnotationReconciler`,
and a real ``asyncio.Event`` proves the shutdown race — with zero real sleeping,
since ``interval_seconds=0`` makes the interval wait resolve immediately every pass.
"""

from __future__ import annotations

import asyncio

import pytest

from blizzard.hub.app import _run_annotation_loop

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
        asyncio.gather(_run_annotation_loop(reconciler, 0, shutdown), _stop_once_three_sweeps_land()),
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
        asyncio.gather(_run_annotation_loop(reconciler, 0, shutdown), _stop_once_two_sweeps_land()),
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

    task = asyncio.ensure_future(_run_annotation_loop(reconciler, 3600, shutdown))
    await asyncio.sleep(0.05)  # let the loop run its first sweep and enter the interval wait
    assert reconciler.calls == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
