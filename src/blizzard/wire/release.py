"""The release — the holding runner's own mid-graph giving back of a chunk's route.

``POST /api/fleet/chunks/{chunk_id}/release`` answers ``200`` with the ``route_released`` row's id,
``409`` with :class:`ReleaseRefusal`, ``403`` when the caller does not hold the chunk's route or
presents a route token that does not match it, and ``404`` for a chunk the tenant does not hold."""

from __future__ import annotations

from pydantic import BaseModel, Field

from blizzard.foundation.release_refusals import ReleaseRefusalReason
from blizzard.wire.base import WireModel

#: The longest ``reason`` a release accepts.
RELEASE_REASON_MAX_LENGTH = 64


class ReleaseRequest(BaseModel):
    """Give the chunk's route back. ``reason`` is the caller's own, recorded and never interpreted."""

    #: The chunk's epoch as the runner last saw it.
    epoch: int
    #: The chunk's live route capability token, as a completion carries it.
    route_token: str | None = None
    reason: str = Field(min_length=1, max_length=RELEASE_REASON_MAX_LENGTH)


class ReleaseResponse(WireModel):
    """The accepted release: the ``route_released`` row it wrote."""

    chunk_id: str
    released_id: int


class ReleaseRefusal(WireModel):
    """The ``409`` body: the chunk's state refuses the release."""

    chunk_id: str
    reason: ReleaseRefusalReason
    detail: str
