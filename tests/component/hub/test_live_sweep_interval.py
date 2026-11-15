"""Committed settings changes interrupt a timed sweep's old interval."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.hub.app import Sweep
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.scaffolding import build_hub, hub_level_connections


async def test_committed_shorter_interval_wakes_an_existing_wait(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    settings = build_hub_settings(
        hub_level_connections(hub.engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), footing
    )
    settings.authoring.apply(
        Change("edit", sets={"annotation_interval_seconds": 3600}), if_match=0, door="api", actor="test"
    )
    loop = asyncio.get_running_loop()
    waiting, second = asyncio.Event(), asyncio.Event()
    calls = 0
    pass_times: list[float] = []

    def reconcile() -> None:
        nonlocal calls
        calls += 1
        pass_times.append(time.monotonic())
        if calls == 2:
            loop.call_soon_threadsafe(second.set)

    ticks = 0

    def timer() -> float:
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            waiting.set()
        return time.monotonic()

    shutdown = asyncio.Event()
    sweep = Sweep(
        SweepName.FORGE_STATUS,
        reconcile,
        3600,
        shutdown,
        timer=timer,
        live_interval=lambda: footing.resolve(settings.reader.snapshot().entries).annotation_interval_seconds,
        subscribe=settings.authoring.subscribe,
        interval_key="annotation_interval_seconds",
    )
    task = asyncio.create_task(sweep.run())
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        settings.authoring.apply(
            Change("edit", sets={"annotation_interval_seconds": 1}), if_match=1, door="api", actor="test"
        )
        await asyncio.wait_for(second.wait(), 3)
        assert 0.8 <= pass_times[1] - pass_times[0] < 3
    finally:
        shutdown.set()
        await task
