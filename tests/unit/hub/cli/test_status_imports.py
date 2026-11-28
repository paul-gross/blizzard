"""What ``blizzard hub status`` loads, in a fresh interpreter: its own command graph and nothing of its siblings'."""

from __future__ import annotations

from tests.support.loaded_modules import loaded_after_running

#: Nothing listens here, so each verb fails at its first request, having loaded everything it needs.
_NOWHERE = ["--hub-url", "http://127.0.0.1:1"]

_DAEMON_STACKS = (
    "blizzard.hub.app",
    "blizzard.hub.composition",
    "blizzard.hub.domain",
    "blizzard.hub.process_root",
    "blizzard.hub.store",
    "blizzard.runner",
    "fastapi",
    "sqlalchemy",
)

_STATUS_CLI_MODULES = {
    "blizzard.hub.cli",
    "blizzard.hub.cli.collaborators",
    "blizzard.hub.cli.command",
    "blizzard.hub.cli.container_command",
    "blizzard.hub.cli.context",
    "blizzard.hub.cli.project_option",
    "blizzard.hub.cli.session_service",
    "blizzard.hub.cli.status",
    "blizzard.hub.cli.views",
    "blizzard.hub.cli.wiring",
}


def _within(loaded: set[str], packages: tuple[str, ...]) -> list[str]:
    return sorted(m for m in loaded if any(m == p or m.startswith(f"{p}.") for p in packages))


def test_status_loads_no_daemon_stack_and_no_sibling_command_or_provider() -> None:
    loaded = loaded_after_running(["hub", "status", *_NOWHERE])

    assert _within(loaded, _DAEMON_STACKS) == []
    assert set(_within(loaded, ("blizzard.hub.cli",))) == _STATUS_CLI_MODULES
    assert sorted(m for m in loaded if m.startswith("blizzard.") and m.endswith(".wiring")) == [
        "blizzard.cli.wiring",
        "blizzard.hub.cli.wiring",
    ]


def test_an_unconverted_verb_loads_neither_the_container_nor_the_command_root() -> None:
    loaded = loaded_after_running(["hub", "chunk", "list", *_NOWHERE])

    assert "blizzard.hub.cli.chunk" in loaded
    assert _within(loaded, ("dishka", "blizzard.foundation.registry", "blizzard.cli.command_root")) == []
