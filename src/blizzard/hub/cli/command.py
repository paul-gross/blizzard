"""The click command base classes every hub client verb declares through, and the root that builds each one's container.

It composes the areas and registers nothing itself: a verb's callback takes collaborators resolved from its own
container, over the values its invocation borrows from the carrier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import click

from blizzard.cli import command_root
from blizzard.cli import wiring as shared
from blizzard.cli.json_output import json_option
from blizzard.cli.operator_trace import OperatorTrace
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.hub.cli import wiring as hub_client
from blizzard.hub.cli.collaborators import HubCliCollaborators
from blizzard.hub.cli.context import DEFAULT_HUB_URL, ENV_HUB_TENANT, ENV_HUB_URL, CliContext


class HubCommand(click.Command):
    """An operator verb: it declares the connection options; its callback takes ``collaborators``, by default ``cli``.

    ``area`` registers what the verb's own area adds. A collaborator named like one of the verb's parameters is
    refused, since it would replace what the operator typed."""

    def __init__(
        self,
        *args: Any,
        collaborators: Mapping[str, type[object]] | None = None,
        area: command_root.Area | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.params = self.connected(self.params)
        self.collaborators: dict[str, type[object]] = (
            {"cli": CliContext} if collaborators is None else dict(collaborators)
        )
        taken = sorted({param.name for param in self.params} & set(self.collaborators))
        if taken:
            raise ValueError(f"{self.name}: collaborators named like parameters: {', '.join(taken)}")
        self._area = area

    @property
    def hub_url_option(self) -> click.Option:
        return click.Option(
            ["--hub-url", "hub_url"],
            default=None,
            help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).",
        )

    @property
    def json_option(self) -> click.Option:
        return json_option()

    @property
    def tenant_option(self) -> click.Option:
        return click.Option(
            ["--tenant", "tenant"],
            default=None,
            envvar=ENV_HUB_TENANT,
            metavar="TENANT_ID",
            help=f"The tenant to act in, by id (default ${ENV_HUB_TENANT}); needed only when you belong to several.",
        )

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        """This verb's own parameters, with the connection options where the verb renders them."""
        raise NotImplementedError

    def options(self, params: dict[str, Any]) -> hub_client.ConnectionOptions:
        """The connection options this verb was invoked with, consumed out of ``params``."""
        raise NotImplementedError

    def areas(self) -> list[command_root.Area]:
        """The areas this verb's container is built from, in registration order."""
        areas: list[command_root.Area] = [shared.register, hub_client.register]
        if self._area is not None:
            areas.append(self._area)
        return areas

    def invoke(self, ctx: click.Context) -> Any:
        carrier = HubCliCollaborators.of(ctx)
        values = ScopeValues(
            (
                supply(shared.CLIENT_FACTORY, carrier.client_factory),
                supply(shared.TRACE_HEADERS, OperatorTrace.source(ctx)),
                supply(shared.WALL_CLOCK, carrier.wall_clock),
                supply(hub_client.SESSION_SERVICE, carrier.sessions),
                supply(hub_client.CONNECTION_OPTIONS, self.options(ctx.params)),
            )
        )
        with command_root.command_scope(
            self.areas(), owner=ctx.command_path, values=values, collaborators=self.collaborators
        ) as resolved:
            ctx.params.update(resolved)
            return super().invoke(ctx)


class FleetCommand(HubCommand):
    """A verb acting inside one tenant that renders a hub response body, so ``--json`` prints it raw;
    ``--tenant`` names the tenant, else the hub acts in the caller's only membership."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [*params, self.json_option, self.tenant_option, self.hub_url_option]

    def options(self, params: dict[str, Any]) -> hub_client.ConnectionOptions:
        return hub_client.ConnectionOptions(
            hub_url=params.pop("hub_url"), as_json=params.pop("as_json"), tenant=params.pop("tenant")
        )


class AdminCommand(HubCommand):
    """A hub-level verb — the hub as a whole, no tenant's data — that renders a hub response body, so
    ``--json`` prints it raw and there is no ``--tenant`` to name."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [*params, self.json_option, self.hub_url_option]

    def options(self, params: dict[str, Any]) -> hub_client.ConnectionOptions:
        return hub_client.ConnectionOptions(hub_url=params.pop("hub_url"), as_json=params.pop("as_json"), tenant=None)


class AuthCommand(HubCommand):
    """A verb over the hub's own ``/api/auth`` surface: it prints a status line, so no ``--json``."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [self.hub_url_option, *params]

    def options(self, params: dict[str, Any]) -> hub_client.ConnectionOptions:
        return hub_client.ConnectionOptions(hub_url=params.pop("hub_url"), as_json=False, tenant=None)
