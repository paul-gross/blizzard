"""Shared lease-scoped authorization plus hub-response detail-unwrapping (``canon:one-owner``).

``authorized_lease`` resolves ``lease_id`` to its active lease and checks the presented token, or raises
the store-free ``503`` / unknown-lease ``404`` / bad-token ``403`` — resolved before any hub call, so an
unauthorized caller never learns the fleet's hub-wiring state. ``upstream_detail`` unwraps an upstream
JSON error body, falling back to raw text, so a forwarded non-200 keeps its own message."""

from __future__ import annotations

import httpx
from fastapi import Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.domain.lease_auth import check_lease_token
from blizzard.runner.store.repository import IReadRunnerStore, LeaseRecord


def authorized_lease(lease_id: str, request: Request) -> LeaseRecord:
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


def upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text
