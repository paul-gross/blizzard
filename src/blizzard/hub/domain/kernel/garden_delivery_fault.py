"""Which delivered garden artifact a rejected delivery faults; the effect model carries it as a plain string."""

from __future__ import annotations

from enum import StrEnum


class GardenDeliveryFault(StrEnum):
    """The artifact to repair — what decides the node a rejected delivery sends the chunk back to."""

    DELTA = "delta"
    PROPOSALS = "proposals"
