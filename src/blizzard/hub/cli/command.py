"""The click command base classes every hub verb declares through."""

from __future__ import annotations

from typing import Any

import click

from blizzard.cli.collaborators import ClientFactory
from blizzard.cli.operator_trace import ITraceHeaders, OperatorTrace
from blizzard.foundation.operator_sessions import IReadSessionStore
from blizzard.hub.cli.collaborators import HubCliCollaborators
from blizzard.hub.cli.context import DEFAULT_HUB_URL, ENV_HUB_TENANT, ENV_HUB_URL, CliContext


class HubCommand(click.Command):
    """An operator verb: it declares the connection options; the callback takes their ``CliContext``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.params = self.connected(self.params)

    @property
    def hub_url_option(self) -> click.Option:
        return click.Option(
            ["--hub-url", "hub_url"],
            default=None,
            help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).",
        )

    @property
    def json_option(self) -> click.Option:
        return click.Option(
            ["--json", "as_json"], is_flag=True, default=False, help="Print the raw response body as JSON."
        )

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

    def context(
        self,
        params: dict[str, Any],
        session_reader: IReadSessionStore,
        trace: ITraceHeaders,
        client_factory: ClientFactory,
    ) -> CliContext:
        """The context those options resolve to, consumed out of ``params``."""
        raise NotImplementedError

    def invoke(self, ctx: click.Context) -> Any:
        carrier = HubCliCollaborators.of(ctx)
        ctx.params["cli"] = self.context(
            ctx.params, carrier.sessions, OperatorTrace.source(ctx), carrier.client_factory
        )
        return super().invoke(ctx)


class FleetCommand(HubCommand):
    """A verb acting inside one tenant that renders a hub response body, so ``--json`` prints it raw;
    ``--tenant`` names the tenant, else the hub acts in the caller's only membership."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [*params, self.json_option, self.tenant_option, self.hub_url_option]

    def context(
        self,
        params: dict[str, Any],
        session_reader: IReadSessionStore,
        trace: ITraceHeaders,
        client_factory: ClientFactory,
    ) -> CliContext:
        return CliContext.of(
            params.pop("hub_url"),
            session_reader,
            trace,
            client_factory,
            params.pop("as_json"),
            tenant=params.pop("tenant"),
        )


class AdminCommand(HubCommand):
    """A hub-level verb — the hub as a whole, no tenant's data — that renders a hub response body, so
    ``--json`` prints it raw and there is no ``--tenant`` to name."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [*params, self.json_option, self.hub_url_option]

    def context(
        self,
        params: dict[str, Any],
        session_reader: IReadSessionStore,
        trace: ITraceHeaders,
        client_factory: ClientFactory,
    ) -> CliContext:
        return CliContext.of(params.pop("hub_url"), session_reader, trace, client_factory, params.pop("as_json"))


class AuthCommand(HubCommand):
    """A verb over the hub's own ``/api/auth`` surface: it prints a status line, so no ``--json``."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [self.hub_url_option, *params]

    def context(
        self,
        params: dict[str, Any],
        session_reader: IReadSessionStore,
        trace: ITraceHeaders,
        client_factory: ClientFactory,
    ) -> CliContext:
        return CliContext.of(params.pop("hub_url"), session_reader, trace, client_factory)


class SessionServiceCommand(AuthCommand):
    """An ``AuthCommand`` that hands the session application service to the controller —
    pinned by
    ``tests/test_cli_surface_contract.py::test_login_and_logout_keep_session_service_off_the_recorded_surface``."""

    def invoke(self, ctx: click.Context) -> Any:
        ctx.params["session_service"] = HubCliCollaborators.of(ctx).sessions
        return super().invoke(ctx)
