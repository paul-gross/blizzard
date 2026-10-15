from __future__ import annotations

import click
import httpx

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.cli.directory import load_host
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.runner.subscriptions.subscription_sampler import MISS_REASON_TEXT, SampleMiss


@click.group("external-usage")
def external_usage_group() -> None:
    """Diagnostics for the host's external-subscription usage sampling."""


@external_usage_group.command("probe")
@click.argument("slug", required=False, default=None)
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
def external_usage_probe(slug: str | None, directory: str) -> None:
    """Sample one declared subscription's rate-limit usage, by SLUG, and print it.

    Read-only, writes nothing. It reads the subscriptions the host declares; SLUG may be left out
    when the host declares exactly one."""
    declared = {declaration.slug: declaration for declaration in load_host(directory).subscriptions}
    if slug is None and len(declared) == 1:
        (slug,) = declared
    if slug is None:
        raise click.ClickException(f"name the subscription to sample (declared: {sorted(declared)})")
    if slug not in declared:
        raise click.ClickException(f"no declared subscription with slug {slug!r} (declared: {sorted(declared)})")
    declaration = declared[slug]
    with httpx.Client() as client:
        sampler = select_sampler(declaration, clock=SystemClock(), http_client=lambda: client)
        if sampler is None:
            click.echo(f"no sample: {declaration.provider!r} (slug {declaration.slug!r}) has no known sampler binding")
            return
        result = sampler.sample()
    if isinstance(result, SampleMiss):
        click.echo(f"no sample: {MISS_REASON_TEXT[result.reason]}")
        return
    snapshot = result
    click.echo(f"sampled at {iso_utc(snapshot.sampled_at)}")
    if not snapshot.windows:
        click.echo("  (no windows reported)")
    for window in snapshot.windows:
        click.echo(
            f"  {window.window}: {window.utilization_pct:.1f}% used, "
            f"resets at {iso_utc(window.resets_at)} (window {window.window_seconds}s)"
        )
