"""Where a timed host's interval comes from at a start: the store's ``tick_seconds`` entry, an unset included, else the
host file's, else the deprecated ``BZ_RUNNER_TICK_SECONDS``, which the seed writes, else the default — with a warning
whenever the variable is set, saying whether it was seeded or ignored (unit tier)."""

from __future__ import annotations

from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.config import DEFAULT_TICK_SECONDS, TickSecondsSource, resolve_tick_seconds

_VARIABLE = {"BZ_RUNNER_TICK_SECONDS": "9"}


def test_the_stores_entry_wins_over_the_file_and_the_variable() -> None:
    source = resolve_tick_seconds(stored=Entry(7), stated=2.0, environ=_VARIABLE)

    assert (source.interval, source.seed) == (7.0, None)
    assert source.warning is not None
    assert "BZ_RUNNER_TICK_SECONDS is deprecated and ignored" in source.warning
    assert "store" in source.warning


def test_a_stored_unset_stands_at_the_default_over_the_file() -> None:
    source = resolve_tick_seconds(stored=Entry(None, at_default=True), stated=2.0, environ={})

    assert source == TickSecondsSource(DEFAULT_TICK_SECONDS)


def test_the_file_wins_over_the_variable_when_the_store_holds_none() -> None:
    source = resolve_tick_seconds(stored=None, stated=2.0, environ=_VARIABLE)

    # The file's value is seeded as every stated key is, not from the variable.
    assert (source.interval, source.seed) == (2.0, None)
    assert source.warning is not None
    assert "ignored" in source.warning
    assert "host file states tick_seconds = 2.0" in source.warning


def test_the_variable_is_seeded_when_neither_the_store_nor_the_file_states_one() -> None:
    source = resolve_tick_seconds(stored=None, stated=None, environ={"BZ_RUNNER_TICK_SECONDS": "0.5"})

    assert (source.interval, source.seed) == (0.5, 0.5)
    assert source.warning is not None
    assert "BZ_RUNNER_TICK_SECONDS is deprecated; seeded as tick_seconds = 0.5" in source.warning


def test_the_default_stands_with_nothing_seeded_and_no_warning_when_none_states_one() -> None:
    assert resolve_tick_seconds(stored=None, stated=None, environ={}) == TickSecondsSource(DEFAULT_TICK_SECONDS)


def test_the_file_or_the_store_alone_earns_no_warning() -> None:
    assert resolve_tick_seconds(stored=None, stated=4.0, environ={}) == TickSecondsSource(4.0)
    assert resolve_tick_seconds(stored=Entry(4), stated=None, environ={}) == TickSecondsSource(4.0)
