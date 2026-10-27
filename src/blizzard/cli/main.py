"""The root ``blizzard`` command group.

Composes the ``hub`` and ``runner`` subgroups; ``blizzard-hub`` and ``blizzard-runner`` are
aliases for them, and each run bare starts its daemon."""

from __future__ import annotations

import click

from blizzard import __version__
from blizzard.cli.lazy_group import LazyGroup

# Each target loads only when run, so `blizzard runner <verb>` never imports the hub.
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
