"""The hub CLI's carrier — the shared collaborators extended with the operator's session service
(``bzh:dependency-injection``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import click

from blizzard.cli.collaborators import CliCollaborators
from blizzard.hub.cli.session_service import SessionService

_PIN = "blizzard.hub.cli.collaborators"


@dataclass(frozen=True)
class HubCliCollaborators(CliCollaborators):
    """What a hub verb acts through: the shared clocks and HTTP factory, plus the session service its token is read
    from and written through. An invoker hands its own to the root group as ``obj``; with none, the composition
    root builds the production service."""

    sessions: SessionService

    @classmethod
    def of(cls, ctx: click.Context) -> HubCliCollaborators:
        """The carrier the root resolved and pinned on the chain's shared ``meta``, so it survives a group replacing
        ``ctx.obj``. Only a root's :meth:`resolve` creates it."""
        pinned: HubCliCollaborators | None = ctx.meta.get(_PIN)
        if pinned is None:
            raise RuntimeError("no hub CLI root resolved the session service for this invocation")
        return pinned

    @classmethod
    def resolve(cls, ctx: click.Context, production_sessions: Callable[[], SessionService]) -> HubCliCollaborators:
        """The invoker's carrier, else the shared collaborators extended with ``production_sessions()``, pinned.

        Called by a composition root, the only place that names the session file."""
        pinned: HubCliCollaborators | None = ctx.meta.get(_PIN)
        if pinned is None:
            pinned = ctx.meta[_PIN] = ctx.find_object(cls) or cls.extending(
                CliCollaborators.of(ctx), production_sessions()
            )
        return pinned

    @classmethod
    def extending(cls, shared: CliCollaborators, sessions: SessionService) -> HubCliCollaborators:
        return cls(
            client_factory=shared.client_factory,
            clock=shared.clock,
            wall_clock=shared.wall_clock,
            monotonic_clock=shared.monotonic_clock,
            sessions=sessions,
        )
