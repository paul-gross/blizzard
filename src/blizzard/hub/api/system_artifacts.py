"""The system-artifact set's own read logic (``ArtifactScope.SYSTEM``) — resolved at call
time straight off the packaged set, never a chunk, a lease, or a store row. Mounted only
under ``blizzard.hub.api.fleet`` (``bzh:system-scope-reads-live``): a worker is the set's
only consumer today (``bzh:app-agnostic-graphs``). The packaged set is injected
(``HubServices.system_artifacts``, ``bzh:dependency-injection``) rather than imported as a
module-level singleton, so a test substitutes a throwaway root instead of monkeypatching."""

from __future__ import annotations

from fastapi import HTTPException, status

from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.wire.system_artifact import SystemArtifactView


def list_system_artifacts(packaged: PackagedSystemArtifacts) -> list[SystemArtifactView]:
    """The full published set, unfiltered — small and read-only, so no paging."""
    return [SystemArtifactView(name=f.name, content=f.text) for f in packaged.files]


def get_system_artifact(name: str, packaged: PackagedSystemArtifacts) -> SystemArtifactView:
    hit = packaged.named(name)
    if hit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no system artifact {name!r}")
    return SystemArtifactView(name=hit.name, content=hit.text)
