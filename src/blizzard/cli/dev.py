"""``blizzard dev <cmd>`` — a hidden developer/operator surface.

Not part of the product CLI (``hub`` / ``runner``): these verbs inspect a store's raw
facts for debugging and crash-recovery verification, so the group is ``hidden=True``.
``check-invariants`` runs the invariant checker (``bzh:invariant-checker``); exit 1 prints each violation.
"""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.foundation.store.invariants import Invariants
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.runner.config import RunnerConfig


@click.group(hidden=True)
def dev() -> None:
    """Developer/operator tooling — store inspection and crash-recovery checks."""


@dev.command("check-invariants")
@click.option("--runner-dir", "runner_dir", default=None, help="Runner runtime directory to check.")
@click.option("--hub-dir", "hub_dir", default=None, help="Hub runtime directory to check.")
@click.option(
    "--allow-external-db",
    "allow_external_db",
    is_flag=True,
    default=False,
    help="Proceed even if --hub-dir's config names a database outside that directory "
    "(issue #234's --dir isolation guard).",
)
def check_invariants_cmd(runner_dir: str | None, hub_dir: str | None, allow_external_db: bool) -> None:
    """Assert both stores' durable invariants (``bzh:invariant-checker``).

    Point at a runner runtime (``--runner-dir``), a hub runtime (``--hub-dir``), or both.
    Exit 0 when every invariant holds; exit 1 listing each violation's slug and detail.
    """
    if runner_dir is None and hub_dir is None:
        raise click.UsageError("pass --runner-dir and/or --hub-dir")

    runner_db = RunnerConfig.load(Path(runner_dir)).db_url if runner_dir is not None else None
    try:
        hub_config = HubConfig.load(Path(hub_dir), allow_external_db=allow_external_db) if hub_dir is not None else None
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    hub_db = hub_config.db_url if hub_config is not None else None

    violations = Invariants(runner_db_url=runner_db, hub_db_url=hub_db).run()
    if not violations:
        click.echo("invariants hold")
        return
    for violation in violations:
        click.echo(str(violation), err=True)
    raise click.ClickException(f"{len(violations)} invariant violation(s)")
