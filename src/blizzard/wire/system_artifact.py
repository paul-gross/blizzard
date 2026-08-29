"""The wire shape for a published system artifact — one of blizzard's own read-only
documents, served under ``ArtifactScope.SYSTEM`` by the fleet's system-artifact routes."""

from __future__ import annotations

from pydantic import BaseModel


class SystemArtifactView(BaseModel):
    """One system artifact as the fleet route serves it — its global name and raw text."""

    name: str
    content: str
