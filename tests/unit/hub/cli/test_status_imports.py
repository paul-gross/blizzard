"""What ``blizzard hub status`` loads, in a fresh interpreter: its own command graph and nothing of its siblings'."""

from __future__ import annotations

from tests.support.loaded_modules import DAEMON_STACKS, loaded_after_running, within

#: Nothing listens here, so each verb fails at its first request, having loaded everything it needs.
_NOWHERE = ["--hub-url", "http://127.0.0.1:1"]

_STATUS_CLI_MODULES = {
    "blizzard.hub.cli",
    "blizzard.hub.cli.collaborators",
    "blizzard.hub.cli.command",
    "blizzard.hub.cli.context",
    "blizzard.hub.cli.project_option",
    "blizzard.hub.cli.session_service",
    "blizzard.hub.cli.status",
    "blizzard.hub.cli.views",
    "blizzard.hub.cli.wiring",
}


def test_status_loads_no_daemon_stack_and_no_sibling_command_or_provider() -> None:
    loaded = loaded_after_running(["hub", "status", *_NOWHERE])

    assert within(loaded, DAEMON_STACKS) == []
    assert set(within(loaded, ("blizzard.hub.cli",))) == _STATUS_CLI_MODULES
    assert sorted(m for m in loaded if m.startswith("blizzard.") and m.endswith(".wiring")) == [
        "blizzard.cli.wiring",
        "blizzard.hub.cli.wiring",
    ]
