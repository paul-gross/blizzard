from __future__ import annotations

from blizzard.wire.base import WireModel


class InvariantViolation(WireModel):
    #: The check's slug — an open vocabulary a client shows as it finds it.
    invariant: str
    detail: str


class InvariantsResponse(WireModel):
    #: Every breach the check found; empty when every invariant holds.
    violations: list[InvariantViolation]
