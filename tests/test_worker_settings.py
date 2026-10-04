"""The worker settings document's ``permissions.deny`` list.

The turn-deferring tools a headless worker must not reach — a settings-document literal
whose effect only a live harness shows (``blizzard:manual-worker-deny-list``).
"""

from __future__ import annotations

from typing import cast

import pytest

from blizzard.runner.harness.claude_code.worker_settings import WorkerSettings
from blizzard.runner.harness.opencode.worker_config import render_worker_config

pytestmark = pytest.mark.unit


def test_worker_settings_denies_exactly_the_turn_deferring_tools() -> None:
    deny = WorkerSettings.of().document["permissions"]["deny"]
    assert deny == [
        "ScheduleWakeup",
        "Monitor",
        "CronCreate",
        "CronDelete",
        "CronList",
        "RemoteTrigger",
        "EndConversation",
    ]


def test_worker_settings_deny_list_excludes_the_sanctioned_polling_tools() -> None:
    deny = WorkerSettings.of().document["permissions"]["deny"]
    assert "TaskOutput" not in deny
    assert "TaskStop" not in deny


def test_each_harness_denies_none_of_the_other_harnesss_tools() -> None:
    claude_denied = set(WorkerSettings.of().document["permissions"]["deny"])
    opencode_denied = set(cast("dict[str, str]", render_worker_config()["permission"]))
    assert claude_denied
    assert opencode_denied
    assert not claude_denied & opencode_denied


def test_worker_settings_pins_disable_all_hooks_off() -> None:
    assert WorkerSettings.of().document["disableAllHooks"] is False
