"""The shared ``--since``/``--until`` flag factories, each attached to a minimal click command and driven
through ``CliRunner``: required versus optional, parsing, help text, and the exact flag spelling.
Unit tier (blizzard:unit-test)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
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
    assert "read in the caller's own local time." in flat
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
