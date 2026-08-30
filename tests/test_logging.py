"""Renderer selection (``bzh:structlog-logging``): explicit arg > env > TTY, and the
traceback each selected chain owes a ``log.exception``.

The call-site convention and the renderers themselves are structlog's; what this
scaffold owns is the *selection* rule, so that is what is asserted here.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
import structlog

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


def _rendered_exception(fmt: LogFormat) -> str:
    """One ``log.exception``-shaped event through ``fmt``'s own processor chain."""
    try:
        raise ValueError("boom")
    except ValueError:
        event_dict: Any = {"event": "sweep failed", "level": "error", "exc_info": sys.exc_info()}
        for processor in [*fmt.exception_processors(), fmt.renderer()]:
            event_dict = processor(None, "error", event_dict)
        return str(event_dict)


@pytest.mark.unit
def test_json_carries_the_traceback_not_a_bare_exc_info_flag() -> None:
    """The JSON renderer formats no traceback of its own, so an unprocessed ``exc_info``
    ships as ``true`` and the trace is lost — which is what the chain must prevent."""
    payload = json.loads(_rendered_exception(Json()))

    assert "Traceback (most recent call last):" in payload["exception"]
    assert "ValueError: boom" in payload["exception"]
    assert "exc_info" not in payload


@pytest.mark.unit
def test_console_leaves_the_traceback_to_its_own_renderer() -> None:
    """``ConsoleRenderer`` formats ``exc_info`` itself, so its chain adds no processor."""
    assert Console().exception_processors() == []

    rendered = _rendered_exception(Console())

    assert "Traceback (most recent call last):" in rendered
    assert "ValueError: boom" in rendered


@pytest.mark.unit
def test_apply_installs_the_formats_exception_processors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chain `apply` configures is the one production logs through — asserting the
    format's own hook leaves a chain that drops the hook green."""
    installed: dict[str, Any] = {}
    monkeypatch.setattr(structlog, "configure", lambda **kwargs: installed.update(kwargs))

    Json().apply()

    processors = installed["processors"]
    assert isinstance(processors[-1], structlog.processors.JSONRenderer)
    assert structlog.processors.format_exc_info in processors[:-1]
