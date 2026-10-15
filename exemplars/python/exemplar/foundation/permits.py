"""The permit vocabulary the hub and its wire share."""

# exemplar: bzh:shared-kernel — a type a wire model carries is defined once here, never in a daemon's domain.

from __future__ import annotations

from enum import StrEnum


class PermitState(StrEnum):
    """A permit's lifecycle state."""

    REQUESTED = "requested"
    GRANTED = "granted"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class PermitVerb(StrEnum):
    """A verb that acts on a permit."""

    GRANT = "grant"
    SUSPEND = "suspend"
    RESUME = "resume"
    REVOKE = "revoke"
