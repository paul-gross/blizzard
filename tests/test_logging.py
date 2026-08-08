"""Renderer selection (``bzh:structlog-logging``): explicit arg > env > TTY.

The call-site convention and the renderers themselves are structlog's; what this
scaffold owns is the *selection* rule, so that is what is asserted here.
"""

from __future__ import annotations

import pytest

from blizzard.foundation.logging import ENV_LOG_FORMAT, Console, Json, LogFormat


@pytest.mark.unit
def test_explicit_arg_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_LOG_FORMAT, "json")
    assert type(LogFormat.of(False)) is Console
    assert type(LogFormat.of(True)) is Json


@pytest.mark.unit
def test_env_json_forces_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_LOG_FORMAT, "JSON")
    assert type(LogFormat.of(None)) is Json


@pytest.mark.unit
def test_env_console_forces_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_LOG_FORMAT, "console")
    assert type(LogFormat.of(None)) is Console


@pytest.mark.unit
def test_falls_through_to_tty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_LOG_FORMAT, raising=False)
    # Under pytest stderr is not a TTY, so the default is JSON.
    assert type(LogFormat.of(None)) is Json
