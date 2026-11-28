"""Production loopback transport for OpenCode compatibility commands."""

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.runner.harness.opencode.compatibility.loopback import ILoopbackTransport, UrllibLoopbackTransport


def loopback_transport() -> ILoopbackTransport:
    return UrllibLoopbackTransport()


def register(registry: Registry) -> None:
    registry.factory(loopback_transport, lifetime=Lifetime.COMMAND)
