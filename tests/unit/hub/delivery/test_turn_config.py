"""Each turn binds a fresh configuration across its asynchronous and threaded work."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.flags import FlagRegistry
from blizzard.hub.config import HubConfig
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.delivery.flow_turn import TurnRunner
from blizzard.hub.domain.delivery.flows import Submission, Turn
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.pass_config import bind_pass_config, current_pass_config


async def test_turn_binds_fresh_config_and_restores_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    first = footing.resolve({})
    second = replace(first, close_forge_writes_enabled=False)
    current = first
    seen: list[HubConfig | None] = []

    async def run(_runner: TurnRunner, _turn: Turn, _submission: Submission) -> None:
        nonlocal current
        seen.append(current_pass_config())
        current = second
        seen.append(await asyncio.to_thread(current_pass_config))

    monkeypatch.setattr(TurnRunner, "_take_bound", run)
    runner = object.__new__(TurnRunner)
    monkeypatch.setattr(runner, "_pass_config", lambda: current, raising=False)
    with bind_pass_config(second):
        await runner.take(cast(Turn, None), cast(Submission, None))
        assert current_pass_config() is second
        await runner.take(cast(Turn, None), cast(Submission, None))
    assert seen == [first, first, second, second]
    assert current_pass_config() is None
