"""The root ``blizzard`` command group.

Composes the two target-namespaced subgroups (``hub``, ``runner``). The
hyphenated aliases ``blizzard-hub`` / ``blizzard-runner`` (for systemd and ``ps``
legibility) point directly at those subgroups, whose bare invocation defaults to
the daemon ``host`` personality.
"""

from __future__ import annotations

import click

from blizzard import __version__
from blizzard.hub.cli import hub
from blizzard.runner.cli import runner


@click.group()
@click.version_option(__version__, prog_name="blizzard")
def blizzard() -> None:
    """Orchestrate autonomous fleets of coding agents."""


blizzard.add_command(hub)
blizzard.add_command(runner)


if __name__ == "__main__":
    blizzard()
