"""``blizzard dev <cmd>`` — a hidden developer/operator surface.

Not part of the product CLI (``hub`` / ``runner``): these verbs inspect a store's raw
facts for debugging and crash-recovery verification. The group is ``hidden=True`` so it
never clutters ``blizzard --help``, but it is a real, tested entrypoint.

``check-invariants`` runs the facts-level invariant checker (``bzh:invariant-checker``,
:mod:`blizzard.foundation.store.invariants`) against a runner store, a hub store, or
both — the same library the kill-9 sweep asserts after every armed crash. Exit 0 means
every durable invariant holds; exit 1 prints each violation and its stable slug.
"""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.foundation.store.invariants import check_invariants
from blizzard.hub.config import HubConfig
from blizzard.runner.config import RunnerConfig


@click.group(hidden=True)
def dev() -> None:
    """Developer/operator tooling — store inspection and crash-recovery checks."""


@dev.command("check-invariants")
@click.option("--runner-dir", "runner_dir", default=None, help="Runner runtime directory to check.")
@click.option("--hub-dir", "hub_dir", default=None, help="Hub runtime directory to check.")
def check_invariants_cmd(runner_dir: str | None, hub_dir: str | None) -> None:
    """Assert both stores' durable invariants (``bzh:invariant-checker``).

    Point at a runner runtime (``--runner-dir``), a hub runtime (``--hub-dir``), or both.
    Exit 0 when every invariant holds; exit 1 listing each violation's slug and detail.
    """
    if runner_dir is None and hub_dir is None:
        raise click.UsageError("pass --runner-dir and/or --hub-dir")

    runner_db = RunnerConfig.load(Path(runner_dir)).db_url if runner_dir is not None else None
    hub_db = HubConfig.load(Path(hub_dir)).db_url if hub_dir is not None else None

    violations = check_invariants(runner_db_url=runner_db, hub_db_url=hub_db)
    if not violations:
        click.echo("invariants hold")
        return
    for violation in violations:
        click.echo(str(violation), err=True)
    raise click.ClickException(f"{len(violations)} invariant violation(s)")
