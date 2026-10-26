"""The sweeps this hub built, by name: one locked pass of any of them, over every tenant or over one, run by the
timer or on request alike."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import domain_model
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.hub.domain.kernel.tenant_fault import TenantFault
from blizzard.hub.domain.sweeps.locks import SweepLocks
from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.tenancy import TenantId


class ISweepsEveryTenant(Protocol):
    """A sweep's pass over every tenant."""

    def sweep(self) -> None: ...


class ISweepsOneTenant(Protocol):
    """A sweep's pass over one tenant, returning the fault that tenant met."""

    def sweep_tenant(self, tenant: TenantId) -> TenantFault | None: ...


class UnknownSweep(Exception):
    """No sweep this hub built goes by the name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"no sweep named {name!r} runs on this hub")
        self.name = name


class SweepServesNoTenant(Exception):
    """The sweep runs over every tenant only."""

    def __init__(self, name: SweepName) -> None:
        super().__init__(f"the {name} sweep runs over every tenant only")
        self.name = name


@domain_model
@dataclass(frozen=True)
class SweepPassResult:
    """How one pass ran: its span on the hub's clock, its outcome, and the fault's message when it failed."""

    started_at: datetime
    finished_at: datetime
    outcome: SweepPassOutcome
    error: str | None = None


class SweepRoster:
    """The built sweeps by name, each pass taken under its :class:`SweepLocks` lock. ``every_tenant`` holds every
    built sweep; ``one_tenant`` the ones a one-tenant pass serves."""

    def __init__(
        self,
        *,
        every_tenant: Mapping[SweepName, ISweepsEveryTenant],
        one_tenant: Mapping[SweepName, ISweepsOneTenant],
        locks: SweepLocks,
        clock: IClock,
    ) -> None:
        self._every_tenant = every_tenant
        self._one_tenant = one_tenant
        self._locks = locks
        self._clock = clock

    def names(self) -> list[SweepName]:
        """The sweeps this hub built, in declaration order."""
        return [name for name in SweepName if name in self._every_tenant]

    def resolve(self, name: str) -> SweepName:
        """The built sweep ``name`` names; raises :class:`UnknownSweep` for any other."""
        try:
            sweep = SweepName(name)
        except ValueError:
            raise UnknownSweep(name) from None
        if sweep not in self._every_tenant:
            raise UnknownSweep(name)
        return sweep

    def pass_every_tenant(self, name: str, tracer: IPlatformTracer | None = None) -> SweepPassResult:
        """One pass of ``name`` over every tenant, under its ``sweep <segment>`` root once its lock is held. A pass
        that raises is logged as ``"sweep failed"`` and answers ``failed``; raises :class:`UnknownSweep`, or
        :class:`SweepInProgress` while any pass of the sweep holds a lock."""
        sweep = self.resolve(name)
        root = (tracer or NoopPlatformTracer()).root
        with self._locks.hold(sweep):
            started_at = self._clock.now()
            try:
                with root(f"sweep {sweep.logger_segment}"):
                    self._every_tenant[sweep].sweep()
            except Exception as exc:
                get_logger(sweep.logger_name).exception("sweep failed")
                return self._ended(started_at, str(exc) or type(exc).__name__)
            return self._ended(started_at, None)

    def pass_one_tenant(self, name: str, tenant: TenantId) -> SweepPassResult:
        """One pass of ``name`` over ``tenant`` alone, answering ``failed`` with the fault that tenant met; raises
        :class:`UnknownSweep`, :class:`SweepServesNoTenant`, or :class:`SweepInProgress` while a pass across every
        tenant or another in ``tenant`` holds its lock."""
        sweep = self.resolve(name)
        if not sweep.serves_one_tenant():
            raise SweepServesNoTenant(sweep)
        with self._locks.hold(sweep, tenant):
            started_at = self._clock.now()
            fault = self._one_tenant[sweep].sweep_tenant(tenant)
            return self._ended(started_at, fault.message if fault is not None else None)

    def _ended(self, started_at: datetime, error: str | None) -> SweepPassResult:
        if error is None:
            return SweepPassResult(started_at, self._clock.now(), SweepPassOutcome.COMPLETED)
        return SweepPassResult(started_at, self._clock.now(), SweepPassOutcome.FAILED, error)
