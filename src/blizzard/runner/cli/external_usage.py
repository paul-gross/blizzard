from __future__ import annotations

from pathlib import Path

import click

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.harness.internal.subscription_sampler_factory import select_sampler


@click.group("external-usage")
def external_usage_group() -> None:
    """Diagnostics for the runner's own external-subscription usage sampling (issue #218)."""


@external_usage_group.command("probe")
@click.argument("slug")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def external_usage_probe(slug: str, directory: str) -> None:
    """Sample one declared subscription's rate-limit usage, by SLUG, and print it.

    Read-only, through the same sampler seam the loop uses — no store write, no tick.
    A runner with no ``[[subscription]]`` entries declares exactly one, ``anthropic``."""
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    declared = {declaration.slug: declaration for declaration in config.resolved_subscriptions()}
    if slug not in declared:
        raise click.ClickException(f"no declared subscription with slug {slug!r} (declared: {sorted(declared)})")
    declaration = declared[slug]
    sampler = select_sampler(declaration)
    if sampler is None:
        click.echo(f"no sample: {declaration.provider!r} (slug {declaration.slug!r}) has no known sampler binding")
        return
    snapshot = sampler.sample()
    if snapshot is None:
        click.echo("no sample: the sampler reported nothing (see the warning log for why)")
        return
    click.echo(f"sampled at {iso_utc(snapshot.sampled_at)}")
    if not snapshot.windows:
        click.echo("  (no windows reported)")
    for window in snapshot.windows:
        click.echo(
            f"  {window.window}: {window.utilization_pct:.1f}% used, "
            f"resets at {iso_utc(window.resets_at)} (window {window.window_seconds}s)"
        )
