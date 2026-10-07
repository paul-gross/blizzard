"""The runner-connection vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class RunnerConnection(StrEnum):
    """An added runner's connection condition, derived at read time: ``never_connected``, added
    at the hub but never registered, so it has no workspace, capabilities, or liveness yet;
    ``online``, registered and heard from within the liveness threshold; ``offline``, registered
    but not heard from within it."""

    NEVER_CONNECTED = "never_connected"
    ONLINE = "online"
    OFFLINE = "offline"
