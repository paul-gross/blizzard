from __future__ import annotations

from pathlib import Path

import click

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter


@click.group("external-usage")
def external_usage_group() -> None:
    """Diagnostics for the runner's own external-subscription usage sampling (issue #218)."""


@external_usage_group.command("probe")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def external_usage_probe(directory: str) -> None:
    """Sample the harness's own subscription rate-limit usage and print it. Read-only.

    Builds the same adapter the reconciliation loop uses and samples through it directly — a diagnostic
    seam-check (issue #218): no store write, no tick, nothing enqueued or delivered."""
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    harness = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        model_aliases=config.model_aliases,
        effort_aliases=config.effort_aliases,
        credentials_path=config.external_usage_credentials_path,
        clock=SystemClock(),
    )
    snapshot = harness.sample_external_subscription_usage()
    if snapshot is None:
        click.echo("no sample: the harness reported nothing (see the warning log for why)")
        return
    click.echo(f"sampled at {iso_utc(snapshot.sampled_at)}")
    if not snapshot.windows:
        click.echo("  (no windows reported)")
    for window in snapshot.windows:
        click.echo(
            f"  {window.window}: {window.utilization_pct:.1f}% used, "
            f"resets at {iso_utc(window.resets_at)} (window {window.window_seconds}s)"
        )
