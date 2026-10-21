"""The swappable collaborators of one CLI process — its HTTP client factory, span clock, and wall clock.

An invoker passes its own as ``obj`` to the root group; with none, the group builds the production ones."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.span_clock import Clock

if TYPE_CHECKING:
    import httpx


def _short_lived_client() -> httpx.Client:
    import httpx

    return httpx.Client()


_PIN = "blizzard.cli.collaborators"


@dataclass(frozen=True)
class CliCollaborators:
    """What a command process's span and request machinery is built from."""

    client_factory: Callable[[], httpx.Client]
    clock: Clock
    #: What a verb judges a time the operator typed against — separate from ``clock``, which stamps spans.
    wall_clock: IClock

    @classmethod
    def production(cls) -> CliCollaborators:
        return cls(client_factory=_short_lived_client, clock=Clock(), wall_clock=SystemClock())

    @classmethod
    def of(cls, ctx: click.Context) -> CliCollaborators:
        """The collaborators the invoker handed down the context chain, else the production ones.

        The first resolution is pinned on the chain's shared ``meta``: the ``hub`` group replaces ``ctx.obj`` with its
        session, so a later lookup would otherwise miss the invoker's carrier."""
        pinned = ctx.meta.get(_PIN)
        if pinned is None:
            pinned = ctx.meta[_PIN] = ctx.find_object(cls) or cls.production()
        return pinned
