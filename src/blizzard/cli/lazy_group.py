"""A command group whose subcommands import on demand.

The CLI is a short-lived process: every command module a group imports up front is paid for
by every verb. A registry built on :class:`LazyGroup` resolves a subcommand's module only when
that subcommand is invoked, listed or described, so a verb that needs little loads little."""

from __future__ import annotations

import importlib
from typing import Any

import click


class LazyGroup(click.Group):
    """A ``click.Group`` that registers each subcommand as ``name -> "module:attribute"``.

    Listing order, help text and parameters are exactly those of an eagerly composed group;
    only the moment the module is imported moves."""

    def __init__(self, *args: Any, lazy: dict[str, str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lazy: dict[str, str] = dict(lazy or {})

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted({*super().list_commands(ctx), *self._lazy})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        target = self._lazy.get(cmd_name)
        if target is None:
            return super().get_command(ctx, cmd_name)
        module, _, attribute = target.partition(":")
        command = getattr(importlib.import_module(module), attribute)
        if not isinstance(command, click.Command):
            raise TypeError(f"{target} is not a click command")
        return command
