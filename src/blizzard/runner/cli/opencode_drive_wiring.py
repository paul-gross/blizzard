"""Borrow the clock used by a selected OpenCode drive command."""

from blizzard.foundation.clock import IMonotonicClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port

CLOCK = port(IMonotonicClock)


def register(registry: Registry) -> None:
    registry.context(CLOCK, lifetime=Lifetime.COMMAND)
