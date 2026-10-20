"""The hub's version and window, as the latest registration reply answered them — held for the process alone,
never stored, since every registration answers them afresh."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.hub.client import HubVersionWindow, RegistrationReply


@domain_model
@dataclass(frozen=True)
class HubVersion:
    """The hub's version and its window — each ``None`` until a hub that names it answers."""

    hub_version: str | None = None
    window: HubVersionWindow | None = None


class ICurrentHubVersion(Protocol):
    def current(self) -> HubVersion: ...


class HubVersionHolder:
    """The process-lifetime :class:`ICurrentHubVersion`, written by the registration step and read by the status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current = HubVersion()

    def current(self) -> HubVersion:
        with self._lock:
            return self._current

    def hold(self, reply: RegistrationReply) -> None:
        with self._lock:
            self._current = HubVersion(hub_version=reply.hub_version, window=reply.window)


def _conforms_current_hub_version(x: HubVersionHolder) -> ICurrentHubVersion:
    return x
