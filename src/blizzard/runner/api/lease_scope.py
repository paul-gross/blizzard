"""Shared lease-scoped authorization for the worker-facing routes."""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.platform_tracing.attributes import annotate_caller
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.tokens import IReadTokenRepository
from blizzard.runner.leases import Lease
from blizzard.runner.leases.lease_auth import LeaseToken


def authorized_lease(lease_id: str, request: Request) -> Lease:
    """Resolve ``lease_id`` to its active lease — or the lease an open takeover names
    — and check the presented token, or raise the store-free ``503`` /
    unknown-lease ``404`` / bad-token ``403`` — before any hub call, so an unauthorized
    caller never learns the fleet's hub-wiring state."""
    wiring = RunnerWiring.of(request)
    lease = wiring.worker_lease(lease_id)
    tokens: IReadTokenRepository = wiring.read_stores().tokens
    if not LeaseToken(presented_lease_token(request), tokens.lease_token_hash(lease_id)).valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"presented token does not authorize lease {lease_id}"
        )
    annotate_caller("worker")
    return lease


def resolved_lease(lease_id: str, request: Request) -> Lease:
    """Resolve ``lease_id`` to its lease regardless of closure — the two hook-fired routes
    that must keep tolerating a replayed or already-closed lease (session-end, heartbeat).

    Distinct from :func:`authorized_lease`: no token check, and it spans every lease this
    runner ever minted rather than only the active one. Raises the unknown-lease ``404``
    for an identifier naming no such lease. This resolves identity, it does not authorize."""
    lease = RunnerWiring.of(request).read_stores().lease_record.lease(lease_id)
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no lease {lease_id}")
    return lease
