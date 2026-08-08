"""Shared lease-scoped authorization for the worker-facing routes."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.store.repository import LeaseRecord


def authorized_lease(lease_id: str, request: Request) -> LeaseRecord:
    """Resolve ``lease_id`` to its active lease and check the presented token, or raise the
    store-free ``503`` / unknown-lease ``404`` / bad-token ``403`` — before any hub call, so an
    unauthorized caller never learns the fleet's hub-wiring state."""
    wiring = RunnerWiring.of(request)
    lease = wiring.active_lease(lease_id)
    if not LeaseToken(presented_lease_token(request), wiring.reads().lease_token_hash(lease_id)).valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"presented token does not authorize lease {lease_id}"
        )
    return lease
