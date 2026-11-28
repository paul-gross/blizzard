"""``blizzard hub permits`` — the tenant's permits, read through a service the command's container owns."""

# exemplar: bzh:dependency-injection — the verb names its collaborator by type and builds nothing.

from __future__ import annotations

import click

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.project_option import project_option
from exemplar.hub.cli import wiring
from exemplar.hub.cli.permit_reader import PermitReader


@click.command(name="permits", cls=FleetCommand, collaborators={"reader": PermitReader}, area=wiring.register)
@project_option(required_by_hub=False)
def permits(reader: PermitReader, project: str | None) -> None:
    """The tenant's permits, newest requested first."""
    reader.show(project)
