"""``blizzard runner token set`` — replace one of the host's runners' hub token, sealed in the host store.

The value is read from stdin only and never printed. A running host seals it over its socket and the runner takes it up
at its next identity check; a stopped host seals it in the store under the store write guard, for its next start."""

from __future__ import annotations

import os
from pathlib import Path

import click
from pydantic import TypeAdapter

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.host_directory import HostDirectory
from blizzard.cli.refusal import refusal_of
from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.secrets.value import SecretValueBlank
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli.daemon import Door, RunnerDaemon
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.runtime import host_answers
from blizzard.runner.cli.store_guard import store_write_guard
from blizzard.runner.composition import build_hub_tokens
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_keys import key_source
from blizzard.runner.host_layout import HostLayout
from blizzard.wire.runner_host import HostRunnerListResponse, HostRunnerView

_VERB = "token set"


@click.group("token")
def token_group() -> None:
    """Replace a runner's hub token, sealed in the host store."""


@token_group.command("set")
@click.argument("directory", required=False, default=None)
@click.option(
    "--dir",
    "dir_option",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Host directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner",
    "runner",
    default=None,
    help="The host's runner whose token to set, by name or id; may be omitted on a host of one runner.",
)
@click.pass_context
def token_set(ctx: click.Context, directory: str | None, dir_option: str, runner: str | None) -> None:
    """Seal the runner's hub token, read from stdin — never an argument — in the host store.

    On a running host the runner takes it up at its next identity check, and a runner its hub refused as retired is
    refused; on a stopped host it takes effect at the next start. Trailing CR/LF is stripped; an empty value is
    refused. DIRECTORY (positional) and --dir are equivalent — pass one. The token is never printed."""
    path = HostDirectory(directory, dir_option).path
    value = _stdin_value()
    door = Door.of(ctx)
    if host_answers(HostLayout(Path(path)).socket_path, door):
        _set_on_running_host(path, door, runner, value)
        return
    _set_on_stopped_host(ctx, path, door, runner, value)


def _stdin_value() -> str:
    value = click.get_text_stream("stdin").read().rstrip("\r\n")
    if not value:
        raise click.ClickException("refusing an empty token — pipe the token on stdin")
    return value


def _set_on_running_host(directory: str, door: Door, runner: str | None, value: str) -> None:
    with RunnerDaemon.reach(_VERB, directory, None, door) as daemon:
        _, answer = daemon.decode_answer(daemon.get_from_host("/api/host/runners"), TypeAdapter(HostRunnerListResponse))
        target = _selected(answer.items, runner, daemon.where)
        response = daemon.send_to_host("put", f"/api/host/runners/{target.runner_id}/token", json_body={"token": value})
    if not response.is_success:
        refusal = refusal_of(response)
        detail = f": {refusal.message}" if refusal.message else ""
        kind = f" ({refusal.type})" if refusal.type else ""
        raise click.ClickException(f"{_VERB}: the host refused runner {target.name}'s token{kind}{detail}")
    when = "its next start" if target.state == "halted" else "its next identity check"
    click.echo(f"runner {target.name} ({target.runner_id}): hub token set in the host store; it takes effect at {when}")


def _selected(items: list[HostRunnerView], runner: str | None, where: str) -> HostRunnerView:
    """The runner ``runner`` names by name or id, halted or served, or the host's only one; a runner with no id has no
    token to set."""
    named = ", ".join(f"{item.name} ({item.runner_id or 'no id'})" for item in items) or "none"
    matched = [item for item in items if runner in (item.name, item.runner_id)] if runner is not None else items
    if not matched:
        raise click.ClickException(f"{_VERB}: the host at {where} holds no runner {runner!r}: {named}")
    if len(matched) > 1:
        raise click.ClickException(f"{_VERB}: the host at {where} holds several runners, so name one: {named}")
    chosen = matched[0]
    if chosen.runner_id is None:
        raise click.ClickException(f"{_VERB}: runner {chosen.name} has no id, so it holds no hub token to set")
    return chosen


def _set_on_stopped_host(ctx: click.Context, directory: str, door: Door, runner: str | None, value: str) -> None:
    config = load_host(directory)
    with click_exception_on(ConfigError):
        entry = config.sole_runner(runner)
    if entry.id is None:
        raise click.ClickException(
            f"runner {entry.name} has no id in {config.config_path}, so it holds no hub token to set"
        )
    clock = CliCollaborators.of(ctx).wall_clock
    with store_write_guard(
        config,
        action=f"set runner {entry.name}'s hub token",
        door=door,
        clock=clock,
        runners={entry.id: entry.name},
        configured=tuple(item.id for item in config.runners if item.id is not None),
        refuse_foreign=True,
    ):
        engine = create_engine_from_url(config.db_url)
        try:
            with click_exception_on(ConfigError):
                tokens = build_hub_tokens(engine, config.root, environ=os.environ, clock=clock, mint=True)
            assert tokens is not None
            with click_exception_on(SecretValueBlank):
                revision = tokens.replace(entry.id, value, retired=False)
        finally:
            engine.dispose()
    click.echo(
        f"runner {entry.name} ({entry.id}): hub token set in the host store at revision {revision}, sealed under "
        f"{key_source(os.environ, config.layout)}; it takes effect at the host's next start, which must resolve the "
        "same key"
    )
