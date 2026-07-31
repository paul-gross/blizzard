"""The runner-local chunk-detail pass-through proxy — ``GET /api/chunks/{id}``, ``POST
.../pause``, ``POST .../resume`` (issue #185).

The machine panel's chunk-detail dock renders in the hub board's own vocabulary — full
chunk id, work-item links, live state, and a working Pause/Resume — so it reads the
:class:`~blizzard.wire.chunk.ChunkDetail` aggregate the board renders, projected down to
:class:`~blizzard.wire.chunk.ChunkHeaderView` (see that class for why), forwarded here
exactly as ``blizzard.runner.api.work_items`` forwards the work-items read: the runner
forwards to the hub's fleet-side counterpart, and the hub calls out with its own
credentials. ``pause`` is the *only* way the panel learns a chunk is paused — that fact
sits independently of ``status`` (a chunk both paused and parked on a question still
derives ``waiting_on_human``), so there is no cheaper local substitute for it.

Pause/Resume are the two board actions this proxy carries — the mutation half of the same
dock. Both forward to new fleet-mounted routes (:mod:`blizzard.hub.api.fleet`) rather than
the board's own anonymous ``/api/chunks/{id}/pause`` — a runner's bearer token is confined
to the fleet router, so forwarding it to the operator path would be rejected outright.

Read-only over its wiring (``bzh:controller-read-only``): all three routes forward to the
hub URL the ``host`` composition root resolved onto ``app.state.config``, carrying the same
``Authorization: Bearer`` credential as every other runner->hub call
(``config.hub_token``). A transport failure to the hub is a ``502``; the hub's own status
(``404`` unknown chunk, ``409`` refused pause/resume) passes through verbatim.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
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


def _upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text


@router.get("/chunks/{chunk_id}", response_model=ChunkHeaderView)
def get_chunk(chunk_id: str, request: Request) -> ChunkHeaderView:
    """Forward a chunk's detail read to the hub — the chunk-detail dock's header subject.

    Forwards to the same fleet-mounted ``ChunkDetail`` read the build worker's own
    envelope poll uses, but validates the response down to :class:`ChunkHeaderView`:
    the header needs only the identity, work-item links, state, and pause fact, and
    projecting here (rather than carrying the full aggregate over this proxy too)
    keeps the transition/artifact history — and the schema collision its
    ``EscalationView`` field would otherwise cause in the runner's own OpenAPI spec —
    out of this route entirely."""
    upstream = _forward(request, "GET", f"/api/fleet/chunks/{chunk_id}")
    if upstream.status_code != status.HTTP_200_OK:
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return ChunkHeaderView.model_validate(upstream.json())


@router.post("/chunks/{chunk_id}/pause", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def pause_chunk(chunk_id: str, request: Request) -> ChunkSummary:
    """Forward the chunk-detail dock's Pause to the hub — kills the active worker, keeps
    the claim (issue #46). ``409`` when the chunk is not in a pausable state."""
    upstream = _forward(request, "POST", f"/api/fleet/chunks/{chunk_id}/pause")
    if upstream.status_code != status.HTTP_202_ACCEPTED:
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return ChunkSummary.model_validate(upstream.json())


@router.post("/chunks/{chunk_id}/resume", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(chunk_id: str, request: Request) -> ChunkSummary:
    """Forward the chunk-detail dock's Resume to the hub — idempotent, never refused."""
    upstream = _forward(request, "POST", f"/api/fleet/chunks/{chunk_id}/resume")
    if upstream.status_code != status.HTTP_202_ACCEPTED:
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return ChunkSummary.model_validate(upstream.json())
