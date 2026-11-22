"""The hub settings' registrations: the ports the admin-settings routes read, served from the borrowed graph.

The entrypoint builds the settings graph over the hub's store and borrows it into the process scope, so each port here
is a member of that one graph and no settings store is constructed in the container. A graph without settings declares
each port absent instead, so a route that reaches one answers the store's unavailability."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.settings.model import IReadHubSettingsAdmin
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def settings_admin(graph: HubSettingsGraph) -> IReadHubSettingsAdmin:
    """The graph's admin store."""
    return graph.store


def settings_reader(graph: HubSettingsGraph) -> HubSettingsReader:
    """The graph's reader."""
    return graph.reader


def settings_authoring(graph: HubSettingsGraph) -> HubSettingsAuthoring:
    """The graph's authoring service."""
    return graph.authoring


def settings_footing(graph: HubSettingsGraph) -> HubSettingsFooting:
    """The graph's footing."""
    return graph.footing


def register(registry: Registry) -> None:
    """Declare the settings ports over the borrowed graph."""
    for build in (settings_admin, settings_reader, settings_authoring, settings_footing):
        registry.factory(build, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the settings ports absent, for a graph with no settings."""
    for missing in (IReadHubSettingsAdmin, HubSettingsReader, HubSettingsAuthoring, HubSettingsFooting):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
