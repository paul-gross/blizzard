"""The runner liveness probe — ``GET /api/health``.

A dependency-free readiness signal, answerable with no store wired."""

from __future__ import annotations

from fastapi import APIRouter

from blizzard import __version__

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "blizzard-runner", "version": __version__}
