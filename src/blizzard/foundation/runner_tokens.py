"""The runner-token refusal vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class RunnerTokenRefusalReason(StrEnum):
    """Why a presented runner bearer token names no runner the hub admits. ``UNKNOWN``: a token the
    hub never issued, as after its data is reset; ``REVOKED``: a revoked token whose runner is not
    retired; ``RETIRED``: a token issued to a runner retired now — retiring revokes the token, so
    this outranks ``REVOKED``."""

    MISSING = "missing"
    UNKNOWN = "unknown"
    REVOKED = "revoked"
    RETIRED = "retired"


class HostTokenRefusalReason(StrEnum):
    """Why a presented host bearer token names no host the hub admits — the runner vocabulary's
    sibling. ``RETIRED`` outranks ``REVOKED``: retiring a host revokes its token."""

    MISSING = "missing"
    UNKNOWN = "unknown"
    REVOKED = "revoked"
    RETIRED = "retired"
