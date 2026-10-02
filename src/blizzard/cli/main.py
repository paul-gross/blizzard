"""The root ``blizzard`` command group.

Composes the target-namespaced subgroups (``hub``, ``runner``); the hyphenated
aliases ``blizzard-hub`` / ``blizzard-runner`` point directly at those subgroups,
whose bare invocation defaults to the daemon ``host`` personality."""

from __future__ import annotations

import click

from blizzard import __version__
from blizzard.cli.lazy_group import LazyGroup

# Each target's module loads only when its subcommand runs, so `blizzard runner <verb>` never
# pays for the hub's imports.
_TARGETS = {
    "hub": "blizzard.hub.cli:hub",
    "runner": "blizzard.runner.cli:runner",
    "dev": "blizzard.cli.dev:dev",
}


@click.group(cls=LazyGroup, lazy=_TARGETS)
@click.version_option(__version__, prog_name="blizzard")
def blizzard() -> None:
    """Orchestrate autonomous fleets of coding agents."""


if __name__ == "__main__":
    blizzard()
