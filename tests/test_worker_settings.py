"""The worker settings document's ``permissions.deny`` list (blizzard#422).

The turn-deferring tools a headless worker must not reach — a settings-document literal
whose effect only a live harness shows (``blizzard:manual-worker-deny-list``).
"""

from __future__ import annotations

from blizzard.runner.harness.worker_settings import WorkerSettings


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
