"""Revision-keyed process-local settings reader."""

from __future__ import annotations

# Guards this process's cache, not the store's write decisions.
# ast-grep-ignore: bzh:store-exclusive-write
from threading import Lock

from blizzard.hub.settings.model import HubSettingsSnapshot, IReadHubSettings


class HubSettingsReader:
    def __init__(self, store: IReadHubSettings) -> None:
        self._store = store
        self._cached: HubSettingsSnapshot | None = None
        self._lock = Lock()

    def snapshot(self) -> HubSettingsSnapshot:
        with self._lock:
            revision = self._store.revision()
            if self._cached is None or self._cached.revision != revision:
                self._cached = self._store.snapshot()
            return self._cached
