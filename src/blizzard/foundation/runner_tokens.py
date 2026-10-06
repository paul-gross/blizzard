"""The runner-token refusal vocabulary — one definition, shared by the hub that judges a
presented runner bearer token, the wire that reports the verdict, and the runner that reads it."""

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
