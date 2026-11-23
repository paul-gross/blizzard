"""The runners area's registrations: its runner and host registry ports over the adapters it is handed, the id-keyed
retired-runner refusal, and the enrollment and report services over them.

Each port aliases the one adapter instance the root hands in, so a reader and a writer in one tenant share it; the area
names no adapter. The served-project reads a runner enrollment resolves against are not the area's to build — they
arrive by key. The store-free graph declares each registration absent, from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.runners.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.runners.host import IReadHostRegistry, IWriteHostRegistry
from blizzard.hub.domain.runners.host_enrollment import HostEnrollmentService
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, IWriteRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def host_report(hosts: IWriteHostRegistry, clock: IClock) -> HostReportService:
    """The tenant's host reports, judged stale at the domain's default threshold."""
    return HostReportService(hosts=hosts, clock=clock)


def register(registry: Registry, *, runners: Ref[IWriteRunnerRegistry], hosts: Ref[IWriteHostRegistry]) -> None:
    """Declare the runner and host registry ports over the adapters handed in, and the services over them, each once
    per tenant."""
    registry.alias(port(IReadRunnerRegistry), runners)
    registry.alias(port(IWriteRunnerRegistry), runners)
    registry.alias(port(IReadHostRegistry), hosts)
    registry.alias(port(IWriteHostRegistry), hosts)
    registry.service(RetiredRunnerGuard, lifetime=Lifetime.TENANT)
    registry.service(RunnerEnrollmentService, lifetime=Lifetime.TENANT)
    registry.service(HostEnrollmentService, lifetime=Lifetime.TENANT)
    registry.factory(host_report, lifetime=Lifetime.TENANT)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    IReadRunnerRegistry,
    IWriteRunnerRegistry,
    IReadHostRegistry,
    IWriteHostRegistry,
    RetiredRunnerGuard,
    RunnerEnrollmentService,
    HostEnrollmentService,
    HostReportService,
)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
