"""``blizzard runner artifact list|get`` — a worker's read of its own node-step
artifacts (issue #127).

Two lease-scoped read routes: ``GET /api/leases/{lease_id}/artifacts`` (the whole set,
resolved latest-by-epoch, both kinds) and ``GET /api/leases/{lease_id}/artifacts/{name}``
(one by ``produces:`` name). The write counterpart is
``POST /api/leases/{lease_id}/attachments`` (``attachments.py``) — the same lease-scoped,
token-authorized shape.

The read is layered exactly like the work-item proxy (``work_items.py``): the worker never
holds hub credentials. This route authorizes the lease token minted at the worker's own
spawn (the same ``X-Blizzard-Lease-Token`` / ``Authorization: Bearer`` the attach edge
takes, via :func:`~blizzard.runner.api.lease_scope.authorized_lease`), resolves the lease
to its ``chunk_id`` through the read-only store on ``app.state``, and forwards to the hub's
runner-authenticated envelope route (``GET /api/fleet/chunks/{id}/envelope``) as the
runner principal (``config.auth_headers()``, issue #86b — the same one-credential path
every runner->hub call rides). The artifacts are filtered straight off the envelope; no
new runner-store persistence, and nothing is cached — the read is live each call.

Status map (attach's, plus the proxy's): ``503`` when the store or the hub wiring is
absent (the store-free app), ``404`` for an unknown/closed lease, ``403`` for a
missing/mismatched token, ``404`` for an unknown artifact name on ``get``, ``409`` when
a bare NAME resolves to more than one producing node (issue #169 — pass ``?node=`` to
disambiguate), and a ``502`` (or the hub's own status verbatim) when the envelope
forward fails. Authorization is
resolved before the hub is consulted, so an unauthorized caller never learns the fleet's
hub-wiring state.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.lease_scope import authorized_lease, upstream_detail
from blizzard.runner.config import RunnerConfig
from blizzard.wire.envelope import EnvelopeArtifact, NodeEnvelope

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.artifacts")
_HUB_TIMEOUT = 15.0


def _envelope_artifacts(chunk_id: str, request: Request) -> list[EnvelopeArtifact]:
    """Forward the chunk's envelope read to the hub and return its artifacts — the
    layered pass-through, runner principal, worker-credential-free."""
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    url = f"{config.hub_url.rstrip('/')}/api/fleet/chunks/{chunk_id}/envelope"
    try:
        upstream = httpx.get(url, headers=config.auth_headers(), timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("artifacts proxy could not reach the hub", chunk_id=chunk_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
    if upstream.status_code != status.HTTP_200_OK:
        raise HTTPException(status_code=upstream.status_code, detail=upstream_detail(upstream))
    return NodeEnvelope.model_validate(upstream.json()).artifacts


@router.get("/leases/{lease_id}/artifacts", response_model=list[EnvelopeArtifact])
def list_artifacts(lease_id: str, request: Request) -> list[EnvelopeArtifact]:
    """The worker's own node-step inputs — every artifact resolved latest-by-epoch,
    both kinds, kind-discriminated."""
    lease = authorized_lease(lease_id, request)
    return _envelope_artifacts(lease.chunk_id, request)


@router.get("/leases/{lease_id}/artifacts/{name}", response_model=EnvelopeArtifact)
def get_artifact(lease_id: str, name: str, request: Request, node: str | None = None) -> EnvelopeArtifact:
    """One artifact by ``produces:`` name; ``404`` when this node-step has none by that
    name (optionally narrowed to one from ``node``, the producing node's name).

    More than one upstream node can emit the same ``produces:`` name (issue #169) — a
    bare NAME that resolves to more than one candidate is ``409``, naming the
    producing nodes, rather than silently returning an arbitrary one; ``?node=`` picks
    a specific one."""
    lease = authorized_lease(lease_id, request)
    matches = [a for a in _envelope_artifacts(lease.chunk_id, request) if a.name == name]
    if node is not None:
        matches = [a for a in matches if a.node_name == node]
    if not matches:
        qualifier = f" from node {node!r}" if node is not None else ""
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no artifact {name!r}{qualifier} for this node-step"
        )
    if len(matches) > 1:
        candidates = sorted({a.node_name for a in matches})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"artifact {name!r} is ambiguous — produced by nodes: {', '.join(candidates)} "
            "(pass --node to disambiguate)",
        )
    return matches[0]
