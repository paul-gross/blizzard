"""The hub liveness probe — ``GET /api/health``.

A dependency-free readiness signal the service tier, the board, and ``winter
service … --wait`` can poll. Deeper status (chunks, questions, runners) is derived
from facts and served by the fleet routers the backend builder fills in.
"""

from __future__ import annotations

from fastapi import APIRouter

from blizzard import __version__

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "blizzard-hub", "version": __version__}
