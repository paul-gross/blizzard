from __future__ import annotations

from pathlib import Path

import click
import httpx

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.config import LEGACY_ANTHROPIC_SLUG, ConfigError, RunnerConfig
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.runner.subscriptions.subscription_sampler import SampleMiss, SampleMissReason

# Operator-facing text per closed-set miss reason (blizzard#504) — what to do about it, not the machine word.
_MISS_REASON_TEXT: dict[SampleMissReason, str] = {
    SampleMissReason.CREDENTIAL_LAPSED: "credential lapsed: log in again",
    SampleMissReason.CREDENTIAL_UNREADABLE: "credential unreadable",
    SampleMissReason.ENDPOINT_UNREACHABLE: "endpoint unreachable",
    SampleMissReason.RESPONSE_UNPARSEABLE: "response unparseable",
}


@click.group("external-usage")
def external_usage_group() -> None:
    """Diagnostics for the runner's own external-subscription usage sampling (issue #218)."""


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

    Read-only, writes nothing. SLUG defaults to the legacy ``anthropic`` declaration
    every runner still carries."""
    slug = slug or LEGACY_ANTHROPIC_SLUG
    try:
        config = RunnerConfig.load(Path(directory))
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    declared = {declaration.slug: declaration for declaration in config.resolved_subscriptions()}
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
        click.echo(f"no sample: {_MISS_REASON_TEXT[result.reason]}")
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
