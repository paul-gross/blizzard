"""The runner liveness probe — ``GET /api/health``.

A dependency-free readiness signal the service tier and ``winter service …
--wait`` can poll. The machine-local view (capacities, held environments, open
asks) is derived from facts and served by the runner routers the backend builder
fills in.
"""

from __future__ import annotations

from fastapi import APIRouter

from blizzard import __version__

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "blizzard-runner", "version": __version__}
