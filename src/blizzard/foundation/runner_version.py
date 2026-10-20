"""A runner's version standing — one definition, shared by the hub and its wire."""

from __future__ import annotations

from enum import StrEnum


class VersionRefusalReason(StrEnum):
    """Why the hub's compatibility window refuses a runner's version."""

    #: The runner's line is below the window's floor.
    TOO_OLD = "too-old"
    #: The runner's line is above the hub's own.
    TOO_NEW = "too-new"
    #: The runner reports no version, and the window's floor no longer admits one that reports none.
    UNVERSIONED = "unversioned"


class RunnerVersionStanding(StrEnum):
    """Where a runner's latest registration stands against the hub's window."""

    #: It reported a version the window admits.
    IN_WINDOW = "in_window"
    #: It reported no version, and the window admitted it anyway.
    UNVERSIONED = "unversioned"
    #: The window refused it; it claims nothing until a later registration is admitted.
    REFUSED = "refused"
