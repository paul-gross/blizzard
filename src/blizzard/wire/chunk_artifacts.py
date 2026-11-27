"""The slice of a hub chunk detail a worker's read of superseded artifact epochs decodes."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.chunk import ArtifactView


class ChunkArtifactsSource(BaseModel):
    """The artifacts slice of a hub ``ChunkDetail`` payload — every epoch of every series. Never a
    FastAPI ``response_model``, decoded with pydantic's default ``extra="ignore"``. ``artifacts``
    is **required**, so a rename fails loudly rather than decoding as "nothing produced"."""

    artifacts: list[ArtifactView]
