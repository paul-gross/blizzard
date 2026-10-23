"""The swappable collaborators of one CLI process — its HTTP client factory, span clock, and wall clock.

An invoker passes its own as ``obj`` to the root group; with none, the group builds the production ones."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import click
from httpx2 import Client, ConnectError, HTTPError, HTTPStatusError, HTTPTransport, Response

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.span_clock import Clock

__all__ = [
    "CliCollaborators",
    "Client",
    "ClientFactory",
    "ConnectError",
    "HTTPError",
    "HTTPStatusError",
    "Response",
]


class ClientFactory(Protocol):
    """Builds one HTTP client; a bare call is the short-lived default."""

    def __call__(
        self,
        *,
        base_url: str = "",
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        uds: str | None = None,
    ) -> Client: ...


def _short_lived_client(
    *,
    base_url: str = "",
    timeout: float | None = None,
    headers: Mapping[str, str] | None = None,
    uds: str | None = None,
) -> Client:
    kwargs: dict[str, object] = {"base_url": base_url, "headers": headers}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if uds is not None:
        kwargs["transport"] = HTTPTransport(uds=uds)
    return Client(**kwargs)  # type: ignore[arg-type]


_PIN = "blizzard.cli.collaborators"


@dataclass(frozen=True)
class CliCollaborators:
    """What a command process's span and request machinery is built from."""

    client_factory: ClientFactory
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
