"""The system-artifact set's own read logic (``ArtifactScope.SYSTEM``) — resolved at call
time straight off the packaged set, never a chunk, a lease, or a store row. Mounted only
under ``blizzard.hub.api.fleet`` (``bzh:system-scope-reads-live``): a worker is the set's
only consumer today (``bzh:app-agnostic-graphs``)."""

from __future__ import annotations

from fastapi import HTTPException, status

from blizzard.hub.system_artifacts import PACKAGED
from blizzard.wire.system_artifact import SystemArtifactView


def list_system_artifacts() -> list[SystemArtifactView]:
    """The full published set, unfiltered — small and read-only, so no paging."""
    return [SystemArtifactView(name=f.name, content=f.text) for f in PACKAGED.files]


def get_system_artifact(name: str) -> SystemArtifactView:
    hit = PACKAGED.named(name)
    if hit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no system artifact {name!r}")
    return SystemArtifactView(name=hit.name, content=hit.text)
