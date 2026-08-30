"""The click command base classes every hub verb declares through (issue #104)."""

from __future__ import annotations

from typing import Any

import click

from blizzard.hub.cli.context import DEFAULT_HUB_URL, ENV_HUB_URL, CliContext


class HubCommand(click.Command):
    """An operator verb (issue #104): it declares the connection options; the callback takes their ``CliContext``."""

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

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        """This verb's own parameters, with the connection options where the verb renders them."""
        raise NotImplementedError

    def context(self, params: dict[str, Any]) -> CliContext:
        """The context those options resolve to, consumed out of ``params``."""
        raise NotImplementedError

    def invoke(self, ctx: click.Context) -> Any:
        ctx.params["cli"] = self.context(ctx.params)
        return super().invoke(ctx)


class FleetCommand(HubCommand):
    """A verb that renders a hub response body, so ``--json`` prints it raw."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [*params, self.json_option, self.hub_url_option]

    def context(self, params: dict[str, Any]) -> CliContext:
        return CliContext.of(params.pop("hub_url"), params.pop("as_json"))


class AuthCommand(HubCommand):
    """A verb over the hub's own ``/api/auth`` surface: it prints a status line, so no ``--json``."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [self.hub_url_option, *params]

    def context(self, params: dict[str, Any]) -> CliContext:
        return CliContext.of(params.pop("hub_url"))
