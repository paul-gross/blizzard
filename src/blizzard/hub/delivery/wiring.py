"""The delivery area's registrations: the catalogue of the hub's actions, which the entrypoint borrows.

The entrypoint resolves the actions from its environ and supplies them when the process scope opens, so no provider
reads the environment. The catalogue is a projection of that one value, keyed by the domain type, so a domain consumer
takes it without importing this package."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.domain.graph.actions import HubActionCatalogue


def register(registry: Registry) -> None:
    """Declare the catalogue that resolves to the borrowed actions' own."""
    registry.factory(action_catalogue, lifetime=Lifetime.PROCESS)


def action_catalogue(actions: HubActions) -> HubActionCatalogue:
    """The actions' catalogue."""
    return actions.catalogue
