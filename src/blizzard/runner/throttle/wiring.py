"""The throttle area's host registrations: the subscription floor clear, over the usage-limit store it is handed.

The host-free graph declares the clear absent."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.runner.throttle.floor_clear import IRecordSubscriptionFloorClear, SubscriptionFloorClearService

#: What answers a resolution of the clear on a host with no stores.
SUBSCRIPTION_FLOOR_CLEAR_ABSENT = "subscription floor-clear service"


def register(registry: Registry, usage_limits: Ref[IRecordSubscriptionFloorClear]) -> None:
    """Declare the floor-clear write over ``usage_limits`` and the subscription floor clear, once per host."""
    registry.alias(port(IRecordSubscriptionFloorClear), usage_limits)
    registry.service(SubscriptionFloorClearService, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the subscription floor clear absent, for the host-free graph."""
    registry.absent(
        port(SubscriptionFloorClearService), lifetime=Lifetime.PROCESS, label=SUBSCRIPTION_FLOOR_CLEAR_ABSENT
    )
