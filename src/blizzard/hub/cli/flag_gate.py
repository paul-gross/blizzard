"""A hub verb or option behind a hub or fleet flag, asked of the hub it addresses (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import click
import httpx

from blizzard.cli.flag_guard import flag_command, flag_option
from blizzard.foundation.flags import FlagRegistry
from blizzard.hub.cli.context import CliContext


def hub_flags(ctx: click.Context) -> Mapping[str, bool]:
    """The addressed hub's flags answer; empty from a hub too old to answer."""
    cli: CliContext = ctx.params["cli"]
    resp = cli.send("get", "/api/flags")
    if resp.status_code == httpx.codes.NOT_FOUND:
        return {}
    cli.check(resp, "GET /flags")
    return dict(resp.json()["flags"])


def hub_flag_command(
    flag: str, registry: FlagRegistry | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Hold a hub verb behind ``flag``."""
    return flag_command(flag, "hub", hub_flags, registry)


def hub_flag_option(
    flag: str, *decls: str, registry: FlagRegistry | None = None, **attrs: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Hold a hub verb's option behind ``flag``."""
    return flag_option(flag, "hub", hub_flags, *decls, registry=registry, **attrs)
