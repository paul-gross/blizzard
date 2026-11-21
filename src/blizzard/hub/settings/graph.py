"""The hub's settings objects as one graph: the store, its reader and authoring service, and their footing."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.model import IReadHubSettingsAdmin
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring


@dataclass(frozen=True)
class HubSettingsGraph:
    store: IReadHubSettingsAdmin
    reader: HubSettingsReader
    authoring: HubSettingsAuthoring
    footing: HubSettingsFooting
