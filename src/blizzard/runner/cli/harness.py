"""``blizzard runner harness`` — operator view of the harness-config bundle."""

from __future__ import annotations

import tomllib
from pathlib import Path

import click

from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.harness.bundle import published_snapshot
from blizzard.runner.harness.wiring import HARNESS_CATALOG, declared, inspect_harness_bundle, shared_inputs


@click.group("harness")
def harness_group() -> None:
    """Operator: the harness-config bundle this runtime loads."""


def _autonomy_source(config: RunnerConfig) -> str:
    overrides = [override for s in config.harness_sections if (override := s.autonomy_override()) is not None]
    if overrides:
        return overrides[0]
    harness = tomllib.loads(config.config_path.read_text()).get("harness")
    return "[harness] autonomy" if isinstance(harness, dict) and "autonomy" in harness else "default"


@harness_group.command("status")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def harness_status(directory: str) -> None:
    """Report the bundle sources, the published snapshot, and the autonomy setting.

    Prints names and paths only, never file contents. Exits 1 when a bundle is configured
    but no snapshot is published."""
    try:
        config = RunnerConfig.load(Path(directory))
        click.echo(f"autonomy: {config.autonomy} (from {_autonomy_source(config)})")
        shared = shared_inputs(config.harness_settings)
        spawn_root = build_workspace_provider(config.workspace_settings).spawn_root()
        for declaration, section in declared(config.harness_sections):
            _echo(declaration.diagnostics(section, shared, spawn_root=spawn_root))
        if config.harness_config_dir is None:
            click.echo("config_dir: none")
            for declaration, section in declared(config.harness_sections):
                _echo(declaration.unbundled_status(section))
            return
        click.echo(f"config_dir: {config.harness_config_dir}")
        sources = inspect_harness_bundle(config.harness_config_dir)
        for source in sources:
            click.echo(f"{source.dirname}: source {source.source_dir}; entry points: {', '.join(source.entry_points)}")
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    snapshot = published_snapshot(config.root)
    if snapshot is None:
        raise click.ClickException("config_dir is configured but no snapshot is published; restart the runner")
    click.echo(f"snapshot: {snapshot}")
    for declaration in HARNESS_CATALOG:
        _echo(declaration.snapshot_status(snapshot, sources))


def _echo(lines: tuple[str, ...]) -> None:
    for line in lines:
        click.echo(line)
