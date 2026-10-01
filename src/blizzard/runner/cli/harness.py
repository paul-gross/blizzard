"""``blizzard runner harness`` — operator view of the harness-config bundle."""

from __future__ import annotations

import tomllib
from pathlib import Path

import click

from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.harness.bundle import published_snapshot
from blizzard.runner.harness.bundle_layouts import inspect_harness_bundle


@click.group("harness")
def harness_group() -> None:
    """Operator: the harness-config bundle this runtime loads."""


def _autonomy_source(config: RunnerConfig) -> str:
    if config.harness_permission_mode is not None:
        return "legacy harness_permission_mode"
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
        if config.harness_config_dir is None:
            click.echo("config_dir: none")
            click.echo(f"worker settings file in effect: {config.worker_settings_path}")
            click.echo(f"opencode worker config in effect: {config.opencode_worker_config_path}")
            return
        click.echo(f"config_dir: {config.harness_config_dir}")
        for source in inspect_harness_bundle(config.harness_config_dir):
            click.echo(f"{source.dirname}: source {source.source_dir}; entry points: {', '.join(source.entry_points)}")
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    snapshot = published_snapshot(config.root)
    if snapshot is None:
        raise click.ClickException("config_dir is configured but no snapshot is published; restart the runner")
    click.echo(f"snapshot: {snapshot}")
