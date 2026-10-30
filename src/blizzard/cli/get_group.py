"""A command group whose read-one verb is ``get``, with ``show`` and ``read`` as hidden aliases.

Every client verb that reads one thing is named ``get``. The other spellings people reach for
resolve to it, so none is a surprise, while ``--help`` and completion list only ``get``."""

from __future__ import annotations

import click

# The one place the alias spellings live.
GET_ALIASES: tuple[str, ...] = ("show", "read")

GET_VERB = "get"


class GetGroup(click.Group):
    """A ``click.Group`` that resolves each of :data:`GET_ALIASES` to its ``get`` command.

    The alias resolves to the very command object registered as ``get`` — never a copy — so the
    two behave identically by construction. :meth:`list_commands` never yields an alias."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        if command is None and cmd_name in GET_ALIASES:
            return super().get_command(ctx, GET_VERB)
        return command

    def aliases(self) -> tuple[str, ...]:
        """The hidden spellings this group accepts — empty when it has no ``get``."""
        return GET_ALIASES if GET_VERB in self.commands else ()
