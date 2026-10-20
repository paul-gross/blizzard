"""``blizzard dev <cmd>`` — a hidden developer/operator surface.

Not part of the product CLI (``hub`` / ``runner``): these verbs inspect a store's raw
facts for debugging and crash-recovery verification, so the group is ``hidden=True``.
``check-invariants`` runs the invariant checker (``bzh:invariant-checker``); exit 1 prints each violation.
"""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.runner.config_table import ConfigError as RunnerConfigError
from blizzard.runner.host_config import HostConfig
from blizzard.tools.invariants import Invariants


@click.group(hidden=True)
def dev() -> None:
    """Developer/operator tooling — store inspection and crash-recovery checks."""


@dev.command("check-invariants")
@click.option("--runner-dir", "runner_dir", default=None, help="Host directory whose runner store to check.")
@click.option("--hub-dir", "hub_dir", default=None, help="Hub runtime directory to check.")
@click.option(
    "--allow-external-db",
    "allow_external_db",
    is_flag=True,
    default=False,
    help="Proceed even if --hub-dir's config names a database outside that directory.",
)
def check_invariants_cmd(runner_dir: str | None, hub_dir: str | None, allow_external_db: bool) -> None:
    """Assert both stores' durable invariants (``bzh:invariant-checker``).

    Point at a host directory (``--runner-dir``), a hub runtime (``--hub-dir``), or both.
    Exit 0 when every invariant holds; exit 1 listing each violation's slug and detail.
    """
    if runner_dir is None and hub_dir is None:
        raise click.UsageError("pass --runner-dir and/or --hub-dir")
    registry = packaged_flag_registry()
    strict = strict_config()

    try:
        runner_db = (
            HostConfig.load_for_store(Path(runner_dir), flag_registry=registry, strict=strict).db_url
            if runner_dir is not None
            else None
        )
    except RunnerConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        hub_config = (
            HubConfig.load(Path(hub_dir), allow_external_db=allow_external_db, flag_registry=registry, strict=strict)
            if hub_dir is not None
            else None
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    hub_db = hub_config.db_url if hub_config is not None else None

    # A live operator is always well past any crash-recovery window — unlike the
    # crash sweep's own "immediately after a kill" checkpoint.
    violations = Invariants(runner_db_url=runner_db, hub_db_url=hub_db).run(after_recovery=True)
    if not violations:
        click.echo("invariants hold")
        return
    for violation in violations:
        click.echo(str(violation), err=True)
    raise click.ClickException(f"{len(violations)} invariant violation(s)")
