"""The cadence area's registrations: the clearing of the chore overrides a retiring urgency names, and the one sequence
of every area's clearing the urgency lifecycle takes.

The urgency area declares only the key, because the areas holding a reference to an urgency sit above it; cadence may
import garden, so it composes the routine clearing with its own. The store-free graph declares both absent."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.domain.cadence.services import CadenceUrgencyClearing
from blizzard.hub.domain.garden.routines import RoutineUrgencyClearing
from blizzard.hub.domain.urgency.ports import UrgencyReferenceClearings
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def urgency_reference_clearings(
    routines: RoutineUrgencyClearing, cadences: CadenceUrgencyClearing
) -> UrgencyReferenceClearings:
    """Every clearing a retire runs: the routine defaults, then the chore overrides."""
    return (routines, cadences)


def register(registry: Registry) -> None:
    """Declare the chore clearing and the composed clearings once per tenant."""
    registry.service(CadenceUrgencyClearing, lifetime=Lifetime.TENANT)
    registry.factory(urgency_reference_clearings, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the chore clearing and the composed clearings absent, for the store-free graph."""
    registry.absent(port(CadenceUrgencyClearing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(
        port(cast("type[Any]", UrgencyReferenceClearings)), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE
    )
