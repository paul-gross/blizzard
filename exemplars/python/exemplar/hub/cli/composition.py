"""A stand-in for the ``blizzard hub`` group: it resolves the carrier and loads the permits verb on demand."""

# exemplar: bzh:dependency-injection — production adds the verb's one entry to the hub group's lazy map.

from __future__ import annotations

import click

from blizzard.cli.operator_trace import OperatorGroup
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.hub.cli.collaborators import HubCliCollaborators
from blizzard.hub.cli.session_service import SessionService


@click.group(cls=OperatorGroup, lazy={"permits": "exemplar.hub.cli.permits:permits"}, trace_root="hub")
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Talk to the blizzard hub."""
    HubCliCollaborators.resolve(ctx, lambda: SessionService(SessionFile.of()))
