"""The hub's one live config source: the boot config, the config a pass bound, and the settings graph's resolution."""

from __future__ import annotations

from blizzard.hub.config import HubConfig
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.settings.pass_config import current_pass_config


def resolve_live_config(settings: HubSettingsGraph) -> HubConfig:
    """The config ``settings`` resolves from its current snapshot."""
    snapshot = settings.reader.snapshot()
    return settings.footing.resolve(snapshot.entries)


class HubConfigSource:
    """Where every live reader of the hub's config reads it: ``boot`` alone when the hub holds no settings graph."""

    def __init__(self, boot: HubConfig, settings: HubSettingsGraph | None) -> None:
        self._boot = boot
        self._settings = settings

    @property
    def boot(self) -> HubConfig:
        """The config the process was started with."""
        return self._boot

    def resolved(self) -> HubConfig:
        """The settings graph's resolution, or the boot config without one — what a pass binds."""
        return self._boot if self._settings is None else resolve_live_config(self._settings)

    def current(self) -> HubConfig:
        """The config the running pass bound, else :meth:`resolved`."""
        bound = current_pass_config()
        return bound if bound is not None else self.resolved()
