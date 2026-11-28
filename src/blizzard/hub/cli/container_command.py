"""The hub verbs' composition root: a fleet verb whose callback takes collaborators resolved from its own container.

It composes the areas and registers nothing itself. Only a verb declared through it imports it, so the others load
neither the command root nor the registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import click

from blizzard.cli import command_root
from blizzard.cli import wiring as shared
from blizzard.cli.operator_trace import OperatorTrace
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.hub.cli import wiring as hub_client
from blizzard.hub.cli.collaborators import HubCliCollaborators
from blizzard.hub.cli.command import FleetCommand, HubCommand


class ContainerFleetCommand(FleetCommand):
    """A ``FleetCommand`` naming its ``collaborators`` by type: the callback takes each under the name given.

    ``area`` is the registration of what the verb's own area adds. The connection options are the parameters
    ``FleetCommand`` declares."""

    def __init__(
        self,
        *args: Any,
        collaborators: Mapping[str, type[object]],
        area: command_root.Area | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.collaborators = dict(collaborators)
        self._area = area

    def registry(self) -> command_root.Recorded:
        """What this verb's container is built from, recorded and not yet built."""
        areas: list[command_root.Area] = [shared.register, hub_client.register]
        if self._area is not None:
            areas.append(self._area)
        return command_root.registered(areas)

    def invoke(self, ctx: click.Context) -> Any:
        carrier = HubCliCollaborators.of(ctx)
        options = hub_client.FleetOptions(
            hub_url=ctx.params.pop("hub_url"), as_json=ctx.params.pop("as_json"), tenant=ctx.params.pop("tenant")
        )
        values = ScopeValues(
            (
                supply(shared.CLIENT_FACTORY, carrier.client_factory),
                supply(shared.TRACE_HEADERS, OperatorTrace.source(ctx)),
                supply(hub_client.SESSIONS, carrier.sessions),
                supply(hub_client.FLEET_OPTIONS, options),
            )
        )
        with command_root.command_scope(
            self.registry(), owner=ctx.command_path, values=values, collaborators=self.collaborators
        ) as resolved:
            ctx.params.update(resolved)
            # Past ``HubCommand.invoke``, which would build a second ``CliContext`` outside the container.
            return super(HubCommand, self).invoke(ctx)
