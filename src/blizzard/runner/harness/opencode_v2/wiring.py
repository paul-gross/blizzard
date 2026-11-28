"""OpenCode 2 service transport in a selected command graph."""

from dataclasses import dataclass

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.harness.opencode_v2.transport import IServiceTransport
from blizzard.runner.harness.opencode_v2.transport_wiring import service_transport


@dataclass(frozen=True)
class ServiceOverride:
    service: IServiceTransport | None


SERVICE_OVERRIDE = port(ServiceOverride)


def _service(override: ServiceOverride) -> IServiceTransport:
    return override.service if override.service is not None else service_transport()


def register(registry: Registry) -> None:
    registry.context(SERVICE_OVERRIDE, lifetime=Lifetime.COMMAND)
    registry.factory(_service, lifetime=Lifetime.COMMAND)
