"""A runner verb or option behind a runner flag, asked of the host the verb addresses (``bzh:flag-hides-behavior``)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Any

import click

from blizzard.cli.flag_guard import flag_command, flag_option
from blizzard.foundation.flags import FlagRegistry
from blizzard.runner.cli.daemon import Door, RunnerDaemon


def host_flags(ctx: click.Context) -> Mapping[str, bool]:
    """The addressed host's flags answer; empty from a host too old to answer."""
    with RunnerDaemon.reach("flags", ctx.params["directory"], ctx.params.get("runner_url"), Door.of(ctx)) as daemon:
        resp = daemon.send_to_host("get", "/api/flags")
    return {} if resp.status_code == HTTPStatus.NOT_FOUND else dict(resp.raise_for_status().json()["flags"])


def runner_flag_command(flag: str, *, registry: FlagRegistry) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Hold a runner verb behind ``flag``."""
    return flag_command(flag, "runner", host_flags, registry=registry)


def runner_flag_option(
    flag: str, *decls: str, registry: FlagRegistry, **attrs: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Hold a runner verb's option behind ``flag``."""
    return flag_option(flag, "runner", host_flags, *decls, registry=registry, **attrs)
