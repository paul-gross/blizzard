"""The runner-local chunk-detail pass-through proxy — read, pause, resume (issue #185).

Read-only over its wiring (``bzh:controller-read-only``): all three routes forward to the
hub URL the composition root resolved onto ``app.state.config``, under the fleet-scoped
bearer credential. A transport failure is a ``502``; the upstream status passes verbatim.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.lease_scope import upstream_detail
from blizzard.runner.config import RunnerConfig
from blizzard.wire.chunk import ChunkHeaderView, ChunkSummary

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.chunk_detail")
_HUB_TIMEOUT = 15.0


def _hub_url(request: Request) -> str:
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    return config.hub_url.rstrip("/")


def _forward(request: Request, method: str, path: str) -> httpx.Response:
    config: RunnerConfig = request.app.state.config
    url = f"{_hub_url(request)}{path}"
    try:
        return httpx.request(method, url, headers=config.auth_headers(), timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("chunk-detail proxy could not reach the hub", url=url, error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc


@router.get("/chunks/{chunk_id}", response_model=ChunkHeaderView)
def get_chunk(chunk_id: str, request: Request) -> ChunkHeaderView:
    """Forward a chunk's detail read to the hub — the chunk-detail dock's header subject.

    The upstream aggregate is validated down to :class:`ChunkHeaderView`, keeping the
    transition/artifact history out of this route's own schema entirely."""
    upstream = _forward(request, "GET", f"/api/fleet/chunks/{chunk_id}")
    if upstream.status_code != status.HTTP_200_OK:
        raise HTTPException(status_code=upstream.status_code, detail=upstream_detail(upstream))
    return ChunkHeaderView.model_validate(upstream.json())


@router.post("/chunks/{chunk_id}/pause", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def pause_chunk(chunk_id: str, request: Request) -> ChunkSummary:
    """Forward the chunk-detail dock's Pause to the hub — kills the active worker, keeps
    the claim (issue #46). ``409`` when the chunk is not in a pausable state."""
    upstream = _forward(request, "POST", f"/api/fleet/chunks/{chunk_id}/pause")
    if upstream.status_code != status.HTTP_202_ACCEPTED:
        raise HTTPException(status_code=upstream.status_code, detail=upstream_detail(upstream))
    return ChunkSummary.model_validate(upstream.json())


@router.post("/chunks/{chunk_id}/resume", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(chunk_id: str, request: Request) -> ChunkSummary:
    """Forward the chunk-detail dock's Resume to the hub — idempotent, never refused."""
    upstream = _forward(request, "POST", f"/api/fleet/chunks/{chunk_id}/resume")
    if upstream.status_code != status.HTTP_202_ACCEPTED:
        raise HTTPException(status_code=upstream.status_code, detail=upstream_detail(upstream))
    return ChunkSummary.model_validate(upstream.json())
