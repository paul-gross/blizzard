"""The wire shape for a published system artifact — one of blizzard's own read-only
documents, scoped under ``ArtifactScope.SYSTEM``."""

from __future__ import annotations

from pydantic import BaseModel


class SystemArtifactView(BaseModel):
    """A system artifact's global name and raw text."""

    name: str
    content: str
