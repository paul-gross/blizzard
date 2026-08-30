from __future__ import annotations

import difflib
import json
from pathlib import Path

import click
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import CONFIG_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.harness.workspace_prompts import (
    PACKAGED,
    WORKSPACE_PROMPT_FILENAME,
    UnknownWorkspacePromptSample,
)
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore


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


_OVERRIDE_SOURCE = "store override (PUT /api/workspace-prompt)"


def _packaged_sample(name: str) -> str:
    """The named sample's prose, as a CLI error naming the corpus when there is no such sample."""
    try:
        return PACKAGED.text(name)
    except UnknownWorkspacePromptSample as exc:
        packaged = ", ".join(PACKAGED.names) or "none"
        raise click.ClickException(f"no packaged sample named {name} (packaged: {packaged})") from exc


def _prompt_config(directory: str) -> RunnerConfig:
    try:
        return RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


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
def prompt_install(name: str, directory: str, force: bool) -> None:
    """Copy packaged sample NAME into the runtime root and point the config at the copy.

    The forked shape: the config carries `workspace_prompt_file`, never the package knob, so
    `prompt diff` has a local file to compare. Takes effect on the next runner restart."""
    text = _packaged_sample(name)
    config = _prompt_config(directory)
    destination = config.root / WORKSPACE_PROMPT_FILENAME
    if destination.exists() and not force:
        raise click.ClickException(f"{destination} already exists — pass --force to overwrite it")
    destination.write_text(text)
    _repoint_config(config.root, file_path=str(destination))
    click.echo(f"installed sample {name} to {destination} and set workspace_prompt_file")
    if _stored_override(config) is not None:
        click.echo("a store override stands and wins over this file — clear it first: DELETE /api/workspace-prompt")
        return
    click.echo("restart the runner to apply it — the prompt file is read once at `host` startup")


@prompt_group.command("diff")
@click.argument("name")
@_PROMPT_DIR_OPTION
def prompt_diff(name: str, directory: str) -> None:
    """Diff this runtime's effective workspace prompt against packaged sample NAME.

    Effective means the lane a spawn reads: the store override when one stands, else the
    configured knob. Exits 1 when they differ, so a deploy can check a forked copy for drift."""
    sample = _packaged_sample(name)
    config = _prompt_config(directory)
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
def prompt_status(directory: str) -> None:
    """Report which source the effective workspace prompt comes from, and how large it is.

    Exits 1 when a source is configured but resolves to nothing — the shape a rollback to a
    wheel that does not read the configured knob produces."""
    config = _prompt_config(directory)
    resolved, source = _effective_prompt(config)
    _echo_prompt_status(source, resolved)
    if source == _OVERRIDE_SOURCE:
        click.echo("the override wins over every config knob until it is cleared: DELETE /api/workspace-prompt")
        return
    if source != "none" and not resolved.strip():
        raise click.ClickException(f"{source} is configured but the workspace prompt resolves to nothing")


def _echo_prompt_status(source: str, prompt: str) -> None:
    click.echo(f"workspace prompt: {len(prompt)} characters, from {source}")


def _effective_prompt(config: RunnerConfig) -> tuple[str, str]:
    """The prompt a spawn would read, and the lane it came from — the one definition the
    `prompt` verbs share, mirroring `SpawnPlan._render`'s override-first precedence."""
    override = _stored_override(config)
    if override is not None:
        return override, _OVERRIDE_SOURCE
    try:
        return config.resolved_workspace_prompt(), _configured_source(config)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _configured_source(config: RunnerConfig) -> str:
    """Which config knob layer 2 resolves from, in the precedence `resolved_workspace_prompt` applies."""
    if config.workspace_prompt_package:
        return f'package "{config.workspace_prompt_package}"'
    if config.workspace_prompt_file:
        return f"file {config.workspace_prompt_file}"
    return "inline workspace_prompt" if config.workspace_prompt else "none"


def _stored_override(config: RunnerConfig) -> str | None:
    """The store's runtime override, or ``None``. A read-only query, so a live daemon is no bar."""
    try:
        store = SqlAlchemyRunnerStore(
            create_engine_from_url(config.db_url), RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
        )
        return store.workspace_prompt_override(config.workspace_id)
    except SQLAlchemyError as exc:
        raise click.ClickException(f"could not read the runner store at {config.db_url}: {exc}") from exc


def _repoint_config(root: Path, *, file_path: str) -> None:
    """Point the top-level prompt knobs at an installed copy, leaving the rest of the file alone.

    A targeted line rewrite: regenerating the config would drop every comment and table an
    operator added. A knob the file predates is inserted beside its siblings rather than at the
    region boundary, where it would split a table's comment block off from its header."""
    path = root / CONFIG_FILENAME
    lines = path.read_text().splitlines(keepends=True)
    knobs = {"workspace_prompt_file": json.dumps(file_path), "workspace_prompt_package": '""'}
    end = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    at = end
    for i, line in enumerate(lines[:end]):
        key = line.split("=", 1)[0].strip()
        if key.startswith("workspace_prompt"):
            at = i + 1
        if key in knobs:
            lines[i] = f"{key} = {knobs.pop(key)}\n"
    if knobs:
        _insert_knobs(lines, at=at, boundary=end, knobs=knobs)
    path.write_text("".join(lines))


def _insert_knobs(lines: list[str], *, at: int, boundary: int, knobs: dict[str, str]) -> None:
    """Splice absent knobs in, rewinding off a table's own comment block and terminating the line
    above — an unterminated last line would otherwise concatenate into invalid TOML."""
    if at == boundary:
        while at > 0 and (lines[at - 1].lstrip().startswith("#") or not lines[at - 1].strip()):
            at -= 1
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = [f"{key} = {value}\n" for key, value in knobs.items()]
