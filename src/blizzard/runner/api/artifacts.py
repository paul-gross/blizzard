"""``blizzard runner artifact list|get`` — a worker's read of its own node-step
artifacts (issue #127).

Two lease-scoped read routes: ``GET /api/leases/{lease_id}/artifacts`` (the whole set,
resolved latest-by-epoch, both kinds) and ``GET /api/leases/{lease_id}/artifacts/{name}``
(one by ``produces:`` name). The write counterpart is
``POST /api/leases/{lease_id}/attachments`` (``attachments.py``) — the same lease-scoped,
token-authorized shape.

The read is layered exactly like the PM-item proxy (``pm_items.py``): the worker never
holds hub credentials. This route authorizes the lease token minted at the worker's own
spawn (the same ``X-Blizzard-Lease-Token`` / ``Authorization: Bearer`` the attach edge
takes, via :func:`~blizzard.runner.api.lease_token.presented_lease_token` +
:func:`~blizzard.runner.domain.lease_auth.check_lease_token`), resolves the lease to its
``chunk_id`` through the read-only store on ``app.state``, and forwards to the hub's
runner-authenticated envelope route (``GET /api/fleet/chunks/{id}/envelope``) as the
runner principal (``config.auth_headers()``, issue #86b — the same one-credential path
every runner->hub call rides). The artifacts are filtered straight off the envelope; no
new runner-store persistence, and nothing is cached — the read is live each call.

Status map (attach's, plus the proxy's): ``503`` when the store or the hub wiring is
absent (the store-free app), ``404`` for an unknown/closed lease, ``403`` for a
missing/mismatched token, ``404`` for an unknown artifact name on ``get``, and a ``502``
(or the hub's own status verbatim) when the envelope forward fails. Authorization is
resolved before the hub is consulted, so an unauthorized caller never learns the fleet's
hub-wiring state.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.lease_auth import check_lease_token
from blizzard.runner.store.repository import IReadRunnerStore, LeaseRecord
from blizzard.wire.envelope import EnvelopeArtifact, NodeEnvelope

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.artifacts")
_HUB_TIMEOUT = 15.0


def _authorized_lease(lease_id: str, request: Request) -> LeaseRecord:
    """Resolve ``lease_id`` to its active lease and check the presented token, or raise
    the store-free ``503`` / unknown-lease ``404`` / bad-token ``403``."""
    store: IReadRunnerStore | None = getattr(request.app.state, "runner_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    lease = store.active_lease(lease_id)
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no active lease {lease_id}")
    if not check_lease_token(
        presented_token=presented_lease_token(request), stored_hash=store.lease_token_hash(lease_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"presented token does not authorize lease {lease_id}"
        )
    return lease


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
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return NodeEnvelope.model_validate(upstream.json()).artifacts


@router.get("/leases/{lease_id}/artifacts", response_model=list[EnvelopeArtifact])
def list_artifacts(lease_id: str, request: Request) -> list[EnvelopeArtifact]:
    """The worker's own node-step inputs — every artifact resolved latest-by-epoch,
    both kinds, kind-discriminated."""
    lease = _authorized_lease(lease_id, request)
    return _envelope_artifacts(lease.chunk_id, request)


@router.get("/leases/{lease_id}/artifacts/{name}", response_model=EnvelopeArtifact)
def get_artifact(lease_id: str, name: str, request: Request) -> EnvelopeArtifact:
    """One artifact by ``produces:`` name; ``404`` when this node-step has none by that
    name."""
    lease = _authorized_lease(lease_id, request)
    for artifact in _envelope_artifacts(lease.chunk_id, request):
        if artifact.name == name:
            return artifact
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no artifact {name!r} for this node-step")


def _upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text
