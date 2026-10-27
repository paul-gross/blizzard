from __future__ import annotations

import difflib
from dataclasses import dataclass
from urllib.parse import quote

import click

from blizzard.cli.runtime import click_exception_on
from blizzard.foundation.roles import collaborator
from blizzard.runner.cli.control import ENV_LOCAL_API_URL
from blizzard.runner.cli.daemon import Door, RunnerDaemon, runner_option
from blizzard.runner.cli.directory import load_runner
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.runtime import read_host_stores
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.workspace_prompts import (
    PACKAGED,
    WORKSPACE_PROMPT_FILENAME,
    EffectiveWorkspacePrompt,
    IConfiguredWorkspacePrompts,
    UnknownWorkspacePromptSample,
    WorkspacePromptReader,
)
from blizzard.runner.host_file import repoint_workspace_prompt
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.store.errors import RunnerStoreError


@click.group("prompt")
def prompt_group() -> None:
    """Operator: the packaged workspace-prompt samples, and which one this runtime uses."""


_PROMPT_DIR_OPTION = click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)


_OVERRIDE_SOURCE = "store override (PUT /api/host/workspaces/<workspace>/prompt)"


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


@prompt_group.command("show")
@click.argument("name")
def prompt_show(name: str) -> None:
    """Print packaged sample NAME's prose to stdout."""
    click.echo(_packaged_sample(name))


@prompt_group.command("install")
@click.argument("name")
@_PROMPT_DIR_OPTION
@click.option("--force", is_flag=True, help="Overwrite an existing workspace-prompt.md in the runtime root.")
@runner_option
def prompt_install(name: str, directory: str, force: bool, runner: str | None) -> None:
    """Copy packaged sample NAME into the runtime root and point the workspace at the copy, which
    `prompt diff` then checks for drift. Takes effect on the next runner restart."""
    text = _packaged_sample(name)
    config = load_runner(directory, runner=runner)
    destination = config.root / WORKSPACE_PROMPT_FILENAME
    if destination.exists() and not force:
        raise click.ClickException(f"{destination} already exists — pass --force to overwrite it")
    destination.write_text(text)
    with click_exception_on(ConfigError):
        repoint_workspace_prompt(
            HostLayout(config.root), config.home_workspace.id, file_path=str(destination), strict=config.strict
        )
    click.echo(f"installed sample {name} to {destination} and set the workspace's prompt_file")
    if _effective_prompt_lane(config).source == "override":
        click.echo("a store override stands and wins over this file — clear it first: `blizzard runner prompt clear`")
        return
    click.echo("restart the runner to apply it — the prompt file is read once at `host` startup")


@prompt_group.command("diff")
@click.argument("name")
@_PROMPT_DIR_OPTION
@runner_option
def prompt_diff(name: str, directory: str, runner: str | None) -> None:
    """Diff this runtime's effective workspace prompt against packaged sample NAME.

    Effective means the prompt a new worker starts with: the override `prompt clear` drops when one
    stands, else the configured prompt. Exits 1 when they differ, so a deploy can check a forked copy for drift."""
    sample = _packaged_sample(name)
    config = load_runner(directory, runner=runner)
    local, source = _effective_prompt(config)
    lines = list(
        difflib.unified_diff(
            sample.splitlines(keepends=True),
            local.splitlines(keepends=True),
            fromfile=f"packaged:{name}",
            tofile=source,
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
    """Report which source the effective workspace prompt comes from, and how large it is.

    Exits 1 when a source is configured but resolves to nothing — the shape a rollback to a
    wheel that does not read the configured knob produces."""
    config = load_runner(directory, runner=runner)
    resolved, source = _effective_prompt(config)
    _echo_prompt_status(source, resolved)
    if source == _OVERRIDE_SOURCE:
        click.echo("the override wins over every config knob until it is cleared: `blizzard runner prompt clear`")
        return
    if source != "none" and not resolved.strip():
        raise click.ClickException(f"{source} is configured but the workspace prompt resolves to nothing")


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
    """Drop the store override so the configured workspace prompt resolves again.

    The one `prompt` verb that needs a running runner. Takes effect on subsequent spawns."""
    workspace_id = load_runner(directory, runner=runner).home_workspace.id
    with RunnerDaemon.reach("prompt clear", directory, runner_url, Door.of(ctx)) as daemon:
        daemon.send_to_host("delete", f"/api/host/workspaces/{quote(workspace_id, safe='')}/prompt").raise_for_status()
    click.echo("workspace-prompt override cleared — the configured prompt applies on subsequent spawns")


def _echo_prompt_status(source: str, prompt: str) -> None:
    click.echo(f"workspace prompt: {len(prompt)} characters, from {source}")


@collaborator
@dataclass(frozen=True)
class _ConfiguredPrompts:
    """The runtime's configured lane, read fresh from its config file rather than a running host's registry."""

    config: RunnerConfig

    def prompt(self, workspace_id: str | None) -> str:
        workspace = next((w for w in self.config.workspaces if w.id == workspace_id), None)
        return workspace.resolved_prompt(self.config.root) if workspace is not None else ""


def _conforms_configured_prompts(x: _ConfiguredPrompts) -> IConfiguredWorkspacePrompts:
    return x


def _effective_prompt_lane(config: RunnerConfig) -> EffectiveWorkspacePrompt:
    """The prompt a spawn would read, and the lane it came from. A read-only store query, so a
    live daemon is no bar."""
    try:
        with read_host_stores(config) as stores:
            return WorkspacePromptReader(stores.workspace_prompt, _ConfiguredPrompts(config)).effective(
                config.home_workspace.id
            )
    except RunnerStoreError as exc:
        raise click.ClickException(f"could not read the runner store at {config.db_url}: {exc}") from exc
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _effective_prompt(config: RunnerConfig) -> tuple[str, str]:
    """The effective prompt, and the label of the lane it came from."""
    effective = _effective_prompt_lane(config)
    if effective.source == "override":
        return effective.prompt, _OVERRIDE_SOURCE
    return effective.prompt, config.home_workspace.prompt_source()
