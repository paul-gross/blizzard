"""The swappable collaborators of one CLI process — its HTTP client factory and span clock.

An invoker passes its own as ``obj`` to the root group; with none, the group builds the production ones."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click

from blizzard.foundation.span_clock import Clock

if TYPE_CHECKING:
    import httpx


def _short_lived_client() -> httpx.Client:
    import httpx

    return httpx.Client()


@dataclass(frozen=True)
class CliCollaborators:
    """What a command process's span and request machinery is built from."""

    client_factory: Callable[[], httpx.Client]
    clock: Clock

    @classmethod
    def production(cls) -> CliCollaborators:
        return cls(client_factory=_short_lived_client, clock=Clock())

    @classmethod
    def of(cls, ctx: click.Context) -> CliCollaborators:
        """The collaborators the invoker handed down the context chain, else the production ones."""
        return ctx.find_object(cls) or cls.production()
