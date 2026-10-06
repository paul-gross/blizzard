"""The shared ``--since``/``--until`` flag factories, each attached to a minimal click command and driven
through ``CliRunner``: required versus optional, parsing, help text, and the exact flag spelling.
Unit tier (blizzard:unit-test)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import click
import pytest
from click.testing import CliRunner

from blizzard.cli.window import since_option, until_option

pytestmark = pytest.mark.unit

_OPTIONS = [
    pytest.param(since_option, "since", id="since"),
    pytest.param(until_option, "until", id="until"),
]


def _command(factory: Callable[..., Any], name: str, *, required: bool | None) -> click.Command:
    option = factory() if required is None else factory(required=required)

    @click.command()
    @option
    def probe(**values: Any) -> None:
        click.echo(repr(values[name]))

    return probe


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
@pytest.mark.parametrize("required", [None, False])
def test_an_omitted_optional_flag_is_none(factory: Callable[..., Any], name: str, required: bool | None) -> None:
    result = CliRunner().invoke(_command(factory, name, required=required), [])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "None"


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
def test_an_omitted_required_flag_is_a_usage_error_naming_it(factory: Callable[..., Any], name: str) -> None:
    result = CliRunner().invoke(_command(factory, name, required=True), [])

    assert result.exit_code == 2
    assert f"--{name}" in result.output


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
@pytest.mark.parametrize("required", [False, True])
def test_a_valid_iso_datetime_parses_to_the_naive_local_value(
    factory: Callable[..., Any], name: str, required: bool
) -> None:
    result = CliRunner().invoke(_command(factory, name, required=required), [f"--{name}", "2026-08-12T09:30:15"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == repr(datetime(2026, 8, 12, 9, 30, 15))


def _instant(factory: Callable[..., Any], name: str, value: str) -> datetime:
    result = CliRunner().invoke(_command(factory, name, required=False), [f"--{name}", value])
    assert result.exit_code == 0, result.output
    return eval(result.output.strip(), {"datetime": __import__("datetime")})


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
@pytest.mark.parametrize(
    ("zoned", "utc"),
    [
        ("2026-08-12T09:30:15Z", datetime(2026, 8, 12, 9, 30, 15, tzinfo=UTC)),
        ("2026-08-12T09:30:15+00:00", datetime(2026, 8, 12, 9, 30, 15, tzinfo=UTC)),
        ("2026-08-12T11:30:15+02:00", datetime(2026, 8, 12, 9, 30, 15, tzinfo=UTC)),
        ("2026-08-12T04:00:15-05:30", datetime(2026, 8, 12, 9, 30, 15, tzinfo=UTC)),
        ("2026-08-12T09:30:15.250000Z", datetime(2026, 8, 12, 9, 30, 15, 250000, tzinfo=UTC)),
    ],
)
def test_a_zoned_rfc3339_time_is_the_same_instant_as_its_local_equivalent(
    factory: Callable[..., Any], name: str, zoned: str, utc: datetime
) -> None:
    parsed = _instant(factory, name, zoned)

    assert parsed.tzinfo is None
    assert parsed.astimezone(UTC) == utc


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
def test_z_and_a_zero_offset_agree(factory: Callable[..., Any], name: str) -> None:
    assert _instant(factory, name, "2026-08-12T09:30:15Z") == _instant(factory, name, "2026-08-12T09:30:15+00:00")


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
@pytest.mark.parametrize("value", ["2026-13-01Z", "2026-13-45T00:00:00Z", "2026-08-12T09:30:15+25:00"])
def test_a_malformed_zoned_time_is_a_usage_error(factory: Callable[..., Any], name: str, value: str) -> None:
    result = CliRunner().invoke(_command(factory, name, required=False), [f"--{name}", value])

    assert result.exit_code == 2
    assert f"Invalid value for '--{name}'" in result.output


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
@pytest.mark.parametrize("value", ["yesterday", "2026-13-45", ""])
def test_a_malformed_datetime_is_a_usage_error(factory: Callable[..., Any], name: str, value: str) -> None:
    result = CliRunner().invoke(_command(factory, name, required=False), [f"--{name}", value])

    assert result.exit_code == 2
    assert f"Invalid value for '--{name}'" in result.output
    assert "does not match the formats" in result.output


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
def test_help_shows_the_flag_and_its_description(factory: Callable[..., Any], name: str) -> None:
    result = CliRunner().invoke(_command(factory, name, required=False), ["--help"])

    assert result.exit_code == 0
    flat = " ".join(result.output.split())
    assert f"--{name} " in flat
    assert "a bare time is read in the caller's own local time" in flat
    assert "one ending Z or +hh:mm is that instant." in flat
    assert "Only records" in flat


def test_each_help_names_its_own_bound() -> None:
    since = " ".join(CliRunner().invoke(_command(since_option, "since", required=False), ["--help"]).output.split())
    until = " ".join(CliRunner().invoke(_command(until_option, "until", required=False), ["--help"]).output.split())

    assert "Only records at/after this instant" in since
    assert "Only records before this instant" in until


@pytest.mark.parametrize(("factory", "name"), _OPTIONS)
def test_the_only_flag_spelling_is_the_long_name(factory: Callable[..., Any], name: str) -> None:
    command = _command(factory, name, required=False)
    [param] = [p for p in command.params if isinstance(p, click.Option)]

    assert param.opts == [f"--{name}"]
    assert param.secondary_opts == []
    assert param.name == name
    assert CliRunner().invoke(command, [f"-{name[0]}", "2026-08-12T09:30:15"]).exit_code == 2
