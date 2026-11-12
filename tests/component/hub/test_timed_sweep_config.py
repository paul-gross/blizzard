"""A timed pass shares its configuration with work executed in its thread."""

import asyncio
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
from blizzard.hub.settings.pass_config import current_pass_config
from tests.support.scaffolding import build_hub, hub_level_connections


async def test_timed_pass_keeps_snapshot_after_edit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    settings = build_hub_settings(
        hub_level_connections(hub.engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), footing
    )
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    seen: list[bool] = []

    def config():
        return footing.resolve(settings.reader.snapshot().entries)

    def reconcile() -> None:
        first = current_pass_config()
        assert first is not None
        settings.authoring.apply(
            Change("edit", sets={"close_forge_writes_enabled": False}), if_match=0, door="api", actor="test"
        )
        second = current_pass_config()
        assert second is not None
        seen.extend((first.close_forge_writes_enabled, second.close_forge_writes_enabled))
        loop.call_soon_threadsafe(shutdown.set)

    await Sweep(SweepName.CLOSE_DRAIN, reconcile, 60, shutdown, pass_config=config).run()
    assert seen == [True, True]
    assert config().close_forge_writes_enabled is False
    assert current_pass_config() is None
