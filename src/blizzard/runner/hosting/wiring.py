"""The hosting area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.runner.hosting.ports import IReadHostedRunnerRepository, IWriteHostedRunnerRepository
from blizzard.runner.hosting.retirement import RetirementNotices


def register_host_store_ports(
    registry: Registry,
    hosted_runners: Ref[IWriteHostedRunnerRepository],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadHostedRunnerRepository), hosted_runners)
    registry.alias(port(IWriteHostedRunnerRepository), hosted_runners)


def register_host_infrastructure(registry: Registry) -> None:
    registry.service(RetirementNotices, lifetime=Lifetime.PROCESS)
