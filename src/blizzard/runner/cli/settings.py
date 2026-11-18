"""Operator access to a runner's live settings through its daemon."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import click
from pydantic import TypeAdapter

from blizzard.cli.collaborators import Response
from blizzard.cli.get_group import GetGroup
from blizzard.cli.refusal import refusal_of
from blizzard.runner.cli.control import ENV_LOCAL_API_URL
from blizzard.runner.cli.daemon import Door, RunnerDaemon, runner_option
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import decode_host_text


def _options(command: Any) -> Any:
    command = click.option(
        "--host", "host", is_flag=True, default=False, help="The host's own settings record, not a runner's."
    )(command)
    command = runner_option(command)
    command = click.option("--runner-url", default=None, envvar=ENV_LOCAL_API_URL, help="Runner local API over TCP.")(
        command
    )
    return click.option(
        "--dir", "directory", default=DEFAULT_DIR, envvar=ENV_RUNNER_DIR, help="Runner runtime directory."
    )(command)


def _answer(daemon: RunnerDaemon, response: Response) -> dict[str, Any]:
    if not response.is_success:
        problem = refusal_of(response)
        message = problem.message or "Settings request refused"
        errors = problem.members.get("errors")
        if errors:
            message += f": {json.dumps(errors)}"
        raise click.ClickException(f"{daemon.verb}: {message}")
    return daemon.decode_answer(response, TypeAdapter(dict[str, Any]))[1]


class _Record:
    """Which record a verb addresses: the host's own, at the host root, or a runner's, under its prefix."""

    def __init__(self, daemon: RunnerDaemon, *, host: bool) -> None:
        self._daemon = daemon
        self._host = host

    def send(
        self, method: str, path: str, *, json_body: object | None = None, headers: dict[str, str] | None = None
    ) -> Response:
        if self._host:
            return self._daemon.send_to_host(method, f"/api/host{path}", json_body=json_body, headers=headers)
        return self._daemon.send(method, f"/api{path}", json_body=json_body, headers=headers)


@contextmanager
def _reach(
    ctx: click.Context, verb: str, directory: str, runner_url: str | None, runner: str | None, host: bool
) -> Generator[tuple[RunnerDaemon, _Record]]:
    if host and runner is not None:
        raise click.UsageError("--host and --runner are mutually exclusive: --host names the host's own record")
    with RunnerDaemon.reach(verb, directory, runner_url, Door.of(ctx), runner) as daemon:
        yield daemon, _Record(daemon, host=host)


def _echo(value: object) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


@click.group("settings", cls=GetGroup)
def settings_group() -> None:
    """Read and edit a runner's live settings, or the host's own with --host."""


@settings_group.command("get")
@click.argument("key", required=False)
@_options
@click.pass_context
def get_settings(
    ctx: click.Context, key: str | None, directory: str, runner_url: str | None, runner: str | None, host: bool
) -> None:
    """Read the settings view, or one KEY."""
    with _reach(ctx, "settings get", directory, runner_url, runner, host) as (daemon, record):
        view = _answer(daemon, record.send("get", "/settings"))
    if key is None:
        _echo(view)
        return
    if view.get("settings_error"):
        raise click.ClickException(f"settings get: settings unavailable: {view['settings_error']}")
    for row in view["settings"]:
        if row["key"] == key:
            _echo(row)
            return
    raise click.ClickException(f"settings get: unknown setting {key!r}")


def _write(
    ctx: click.Context, directory: str, runner_url: str | None, runner: str | None, host: bool, body: object
) -> None:
    with _reach(ctx, "settings write", directory, runner_url, runner, host) as (daemon, record):
        view = _answer(daemon, record.send("get", "/settings"))
        response = record.send("patch", "/settings", json_body=body, headers={"If-Match": str(view["revision"])})
        _echo(_answer(daemon, response))


@settings_group.command("set")
@click.argument("key")
@click.argument("value")
@_options
@click.pass_context
def set_settings(
    ctx: click.Context, key: str, value: str, directory: str, runner_url: str | None, runner: str | None, host: bool
) -> None:
    """Set KEY to VALUE, a TOML literal (including inline tables and lists)."""
    try:
        parsed = decode_host_text(f"value = {value}", what="VALUE")["value"]
        json.dumps(parsed, allow_nan=False)
    except (ConfigError, ValueError, TypeError) as error:
        raise click.BadParameter(str(error), param_hint="VALUE") from error
    _write(ctx, directory, runner_url, runner, host, {"set": {key: parsed}, "unset": []})


@settings_group.command("unset")
@click.argument("key")
@_options
@click.pass_context
def unset_settings(
    ctx: click.Context, key: str, directory: str, runner_url: str | None, runner: str | None, host: bool
) -> None:
    """Restore KEY to its default."""
    _write(ctx, directory, runner_url, runner, host, {"set": {}, "unset": [key]})


@settings_group.command("changes")
@_options
@click.pass_context
def changes_settings(
    ctx: click.Context, directory: str, runner_url: str | None, runner: str | None, host: bool
) -> None:
    """Read the settings change history."""
    with _reach(ctx, "settings changes", directory, runner_url, runner, host) as (daemon, record):
        _echo(_answer(daemon, record.send("get", "/settings/changes")))
