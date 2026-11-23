"""The garden area's registrations: the clearing of the routine defaults a retiring urgency names, over the routine
writes it is handed. The store-free graph declares it absent."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.domain.garden.routines import RoutineUrgencyClearing
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def register(registry: Registry) -> None:
    """Declare the routine clearing once per tenant."""
    registry.service(RoutineUrgencyClearing, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the routine clearing absent, for the store-free graph."""
    registry.absent(port(RoutineUrgencyClearing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
