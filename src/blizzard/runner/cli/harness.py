"""``blizzard runner harness`` — operator view of the harness-config bundle."""

from __future__ import annotations

import os

import click

from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.cli.children import command_children
from blizzard.runner.cli.daemon import runner_option
from blizzard.runner.cli.directory import load_runner
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config_table import ConfigError
from blizzard.runner.environments.factory import WorkspaceLaunch, build_workspace_provider
from blizzard.runner.harness.bundle import HarnessBundleNotPublished, require_published
from blizzard.runner.harness.wiring import (
    declared,
    harness_catalog,
    inspect_harness_bundle,
    published_harness_snapshot,
    shared_inputs,
)


@click.group("harness")
def harness_group() -> None:
    """Operator: the harness-config bundle this runtime loads."""


@harness_group.command("status")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@runner_option
@click.pass_context
def harness_status(ctx: click.Context, directory: str, runner: str | None) -> None:
    """Report the bundle sources, the published snapshot, and the autonomy setting.

    Prints names and paths only, never file contents. Exits 1 when a bundle is configured
    but no snapshot is published."""
    config = load_runner(directory, runner=runner)
    try:
        click.echo(f"autonomy: {config.autonomy} (from {config.autonomy_source})")
        tracing = TracingSettings.of(os.environ)
        shared = shared_inputs(config.harness_settings(tracing))
        launch = WorkspaceLaunch(children=command_children(ctx), env=config.tool_env(tracing))
        spawn_roots = dict.fromkeys(
            build_workspace_provider(workspace.settings(config.root), launch=launch).spawn_root()
            for workspace in config.bound_workspaces
        )
        for declaration, section in declared(config.harness_sections):
            _echo(
                tuple(
                    dict.fromkeys(
                        line
                        for spawn_root in spawn_roots
                        for line in declaration.diagnostics(section, shared, spawn_root=spawn_root)
                    )
                )
            )
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
    try:
        snapshot = require_published(config.harness_config_dir, published_harness_snapshot(config.root))
    except HarnessBundleNotPublished as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"snapshot: {snapshot}")
    for declaration in harness_catalog():
        _echo(declaration.snapshot_status(snapshot, sources))


def _echo(lines: tuple[str, ...]) -> None:
    for line in lines:
        click.echo(line)
