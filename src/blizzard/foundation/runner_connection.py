"""The runner-connection vocabulary — one definition, shared by the hub registry that derives a
runner's connection condition and the wire that reports it to operators."""

from __future__ import annotations

from enum import StrEnum


class RunnerConnection(StrEnum):
    """An added runner's connection condition, derived at read time: ``NEVER_CONNECTED``, added
    at the hub but never registered, so it has no workspace, capabilities, or liveness yet;
    ``ONLINE``, registered and heard from within the liveness threshold; ``OFFLINE``, registered
    but not heard from within it."""

    NEVER_CONNECTED = "never_connected"
    ONLINE = "online"
    OFFLINE = "offline"
