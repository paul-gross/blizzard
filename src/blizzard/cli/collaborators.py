"""The swappable collaborators of one CLI process — its HTTP client factory, span clock, and wall and monotonic clocks.

An invoker passes its own as ``obj`` to the root group; with none, the group builds the production ones."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import click
from httpx2 import Client, ConnectError, HTTPError, HTTPStatusError, HTTPTransport, Response

from blizzard.foundation.clock import IClock, IMonotonicClock, SystemClock, SystemMonotonicClock
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
    transport = HTTPTransport(uds=uds) if uds is not None else None
    if timeout is None:
        return Client(base_url=base_url, headers=headers, transport=transport)
    return Client(base_url=base_url, headers=headers, transport=transport, timeout=timeout)


_PIN = "blizzard.cli.collaborators"


@dataclass(frozen=True)
class CliCollaborators:
    """What a command process's span and request machinery is built from."""

    client_factory: ClientFactory
    clock: Clock
    #: What a verb judges a time the operator typed against — separate from ``clock``, which stamps spans.
    wall_clock: IClock
    #: What a verb's bounded poll loop reads elapsed time and sleeps through.
    monotonic_clock: IMonotonicClock

    @classmethod
    def production(cls) -> CliCollaborators:
        return cls(
            client_factory=_short_lived_client,
            clock=Clock(),
            wall_clock=SystemClock(),
            monotonic_clock=SystemMonotonicClock(),
        )

    @classmethod
    def of(cls, ctx: click.Context) -> CliCollaborators:
        """The collaborators the invoker handed down the context chain, else the production ones.

        The first resolution is pinned on the chain's shared ``meta``, so it survives a group replacing ``ctx.obj``."""
        pinned = ctx.meta.get(_PIN)
        if pinned is None:
            pinned = ctx.meta[_PIN] = ctx.find_object(cls) or cls.production()
        return pinned
