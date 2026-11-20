from __future__ import annotations

import difflib
from urllib.parse import quote

import click

from blizzard.cli.get_group import GetGroup
from blizzard.runner.cli.control import ENV_LOCAL_API_URL
from blizzard.runner.cli.daemon import Door, RunnerDaemon, runner_option
from blizzard.runner.cli.directory import load_runner
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.host_records import WORKSPACE, edit_record
from blizzard.runner.cli.runtime import read_host_stores
from blizzard.runner.config import HostWorkspace, RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.workspace_prompts import (
    PACKAGED,
    WORKSPACE_PROMPT_FILENAME,
    UnknownWorkspacePromptSample,
)
from blizzard.runner.host_config import record_prompt
from blizzard.runner.host_records.model import HostRecord, RecordKind
from blizzard.runner.host_records.prompt import PROMPT_SETTINGS, EffectiveWorkspacePrompt
from blizzard.runner.store.errors import RunnerStoreError


@click.group("prompt", cls=GetGroup)
def prompt_group() -> None:
    """Operator: the packaged workspace-prompt samples, and which one this runtime uses."""


_PROMPT_DIR_OPTION = click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)


def _packaged_sample(name: str) -> str:
    """The named sample's prose, as a CLI error naming the corpus when there is no such sample."""
    try:
        return PACKAGED.text(name)
    except UnknownWorkspacePromptSample as exc:
        packaged = ", ".join(PACKAGED.names) or "none"
        raise click.ClickException(f"no packaged sample named {name} (packaged: {packaged})") from exc


@prompt_group.command("list")
def prompt_list() -> None:
    """List the workspace-prompt samples shipped in this wheel."""
    names = PACKAGED.names
    if not names:
        click.echo("no packaged samples")
        return
    for name in names:
        click.echo(f"{name}\t{len(PACKAGED.text(name))} characters")


@prompt_group.command("get")
@click.argument("name")
def prompt_show(name: str) -> None:
    """Print packaged sample NAME's prose to stdout."""
    click.echo(_packaged_sample(name))


@prompt_group.command("install")
@click.argument("name")
@_PROMPT_DIR_OPTION
@click.option("--force", is_flag=True, help="Overwrite an existing workspace-prompt.md in the runtime root.")
@runner_option
@click.pass_context
def prompt_install(ctx: click.Context, name: str, directory: str, force: bool, runner: str | None) -> None:
    """Copy packaged sample NAME into the runtime root and point the workspace's record at the copy, which
    `prompt diff` then checks for drift. Takes effect on the workspace's next spawn."""
    text = _packaged_sample(name)
    config = load_runner(directory, runner=runner)
    destination = config.root / WORKSPACE_PROMPT_FILENAME
    if destination.exists() and not force:
        raise click.ClickException(f"{destination} already exists — pass --force to overwrite it")
    destination.write_text(text)
    body: dict[str, object] = {"prompt_file": str(destination), "prompt": None, "prompt_package": None}
    edit_record(ctx, WORKSPACE, directory, config.home_workspace.id, body, verb="prompt install")
    click.echo(f"installed sample {name} to {destination} and set the workspace's prompt_file")


@prompt_group.command("diff")
@click.argument("name")
@_PROMPT_DIR_OPTION
@runner_option
def prompt_diff(name: str, directory: str, runner: str | None) -> None:
    """Diff this runtime's effective workspace prompt against packaged sample NAME.

    Effective means the prompt a new worker starts with: the one the workspace's record resolves to. Exits 1 when
    they differ, so a deploy can check a forked copy for drift."""
    sample = _packaged_sample(name)
    effective = _effective_prompt(load_runner(directory, runner=runner))
    lines = list(
        difflib.unified_diff(
            sample.splitlines(keepends=True),
            effective.prompt.splitlines(keepends=True),
            fromfile=f"packaged:{name}",
            tofile=effective.origin,
        )
    )
    if not lines:
        click.echo(f"no drift from packaged sample {name}")
        return
    click.echo("".join(lines).rstrip("\n"))
    raise SystemExit(1)


@prompt_group.command("status")
@_PROMPT_DIR_OPTION
@runner_option
def prompt_status(directory: str, runner: str | None) -> None:
    """Report which setting of the workspace's record the effective workspace prompt comes from, and how large it is.

    Exits 1 when a setting is stated but resolves to nothing — the shape a rollback to a wheel that does not read
    the stated setting produces."""
    effective = _effective_prompt(load_runner(directory, runner=runner))
    click.echo(f"workspace prompt: {len(effective.prompt)} characters, from {effective.origin}")
    if effective.source != "default" and not effective.prompt.strip():
        raise click.ClickException(f"{effective.origin} is configured but the workspace prompt resolves to nothing")


@prompt_group.command("clear")
@_PROMPT_DIR_OPTION
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
@runner_option
@click.pass_context
def prompt_clear(ctx: click.Context, directory: str, runner_url: str | None, runner: str | None) -> None:
    """Return the workspace's prompt, prompt_file and prompt_package to their defaults, so it carries no standing
    prompt. Through a running host, or into a stopped host's store. Takes effect on subsequent spawns."""
    workspace_id = load_runner(directory, runner=runner).home_workspace.id
    if runner_url is not None:
        with RunnerDaemon.reach("prompt clear", directory, runner_url, Door.of(ctx)) as daemon:
            route = f"/api/host/workspaces/{quote(workspace_id, safe='')}/prompt"
            daemon.send_to_host("delete", route).raise_for_status()
        click.echo("workspace prompt cleared — the workspace carries no standing prompt on subsequent spawns")
        return
    edit_record(ctx, WORKSPACE, directory, workspace_id, dict.fromkeys(PROMPT_SETTINGS), verb="prompt clear")


def _effective_prompt(config: RunnerConfig) -> EffectiveWorkspacePrompt:
    """The prompt a spawn in the runner's workspace would read, from the workspace's record. A read-only store
    query, so a live daemon is no bar."""
    workspace_id = config.home_workspace.id
    try:
        with read_host_stores(config) as stores:
            record = stores.workspaces.record(workspace_id)
        if record is None:
            # A host never started holds no record yet: its file's table is what its first start seeds.
            workspace = config.home_workspace
            record = HostRecord(RecordKind.WORKSPACE, workspace_id, 0, _prompt_settings(workspace))
        return record_prompt(record, config.root)
    except RunnerStoreError as exc:
        raise click.ClickException(f"could not read the runner store at {config.db_url}: {exc}") from exc
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _prompt_settings(workspace: HostWorkspace) -> dict[str, object]:
    return {name: value for name in PROMPT_SETTINGS if (value := getattr(workspace, name))}
