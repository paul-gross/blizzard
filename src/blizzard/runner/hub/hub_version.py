"""The hub's own version and the compatibility window it judges this runner under, as its latest registration
reply answered them.

Held for the process alone, never stored: every registration answers them afresh, and a hub that predates the
handshake answers neither."""

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
    """The hub version the latest registration reply answered."""

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
        """Answer the version and window ``reply`` names from now on."""
        with self._lock:
            self._current = HubVersion(hub_version=reply.hub_version, window=reply.window)


def _conforms_current_hub_version(x: HubVersionHolder) -> ICurrentHubVersion:
    return x
