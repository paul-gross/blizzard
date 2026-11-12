"""A timed host's interval resolves from the host file's ``tick_seconds``, then the deprecated
``BZ_RUNNER_TICK_SECONDS``, then the default, warning whenever the variable is set (unit tier)."""

from __future__ import annotations

from blizzard.runner.config import DEFAULT_TICK_SECONDS, resolve_tick_seconds


def test_the_file_wins_and_the_set_variable_is_warned_overridden() -> None:
    interval, warning = resolve_tick_seconds(2.0, {"BZ_RUNNER_TICK_SECONDS": "9"})

    assert interval == 2.0
    assert warning is not None
    assert "BZ_RUNNER_TICK_SECONDS is deprecated" in warning
    assert "overridden" in warning


def test_the_variable_is_honored_with_a_warning_when_the_file_states_none() -> None:
    interval, warning = resolve_tick_seconds(None, {"BZ_RUNNER_TICK_SECONDS": "0.5"})

    assert interval == 0.5
    assert warning is not None
    assert "BZ_RUNNER_TICK_SECONDS is deprecated" in warning
    assert "honored" in warning


def test_the_default_stands_with_no_warning_when_neither_states_one() -> None:
    assert resolve_tick_seconds(None, {}) == (DEFAULT_TICK_SECONDS, None)


def test_the_file_alone_earns_no_warning() -> None:
    assert resolve_tick_seconds(4.0, {}) == (4.0, None)
