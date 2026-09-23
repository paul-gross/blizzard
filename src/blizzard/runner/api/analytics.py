"""``GET /api/leases/{lease_id}/analytics/...`` — a worker's own routine-run read of the
six operator counts/spend summaries over a window it names (blizzard#545). Lease-scoped
and token-authorized, then forwarded to the hub as the runner principal — the same
pluggable-seam shape ``runner/api/garden.py``'s reads take. ``since``/``until`` are
carried through unvalidated, exactly like garden's own ``state``: the hub is the one
source of truth for the window's required/UTC-instant shape (``bzh:pluggable-seams``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.analytics import AnalyticsCountsResponse, AnalyticsSpendResponse

router = APIRouter(prefix="/api", tags=["runner"])


def _window_params(since: str | None, until: str | None) -> dict[str, str]:
    return {k: v for k, v in {"since": since, "until": until}.items() if v is not None}


def _counts(
    lease_id: str, request: Request, suffix: str, since: str | None, until: str | None
) -> AnalyticsCountsResponse:
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "analytics").get(
        f"/api/fleet/chunks/{lease.chunk_id}/analytics/counts/{suffix}",
        params=_window_params(since, until),
        chunk_id=lease.chunk_id,
    )
    return AnalyticsCountsResponse.model_validate(upstream.json())


def _spend(
    lease_id: str, request: Request, suffix: str, since: str | None, until: str | None
) -> AnalyticsSpendResponse:
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "analytics").get(
        f"/api/fleet/chunks/{lease.chunk_id}/analytics/spend/{suffix}",
        params=_window_params(since, until),
        chunk_id=lease.chunk_id,
    )
    return AnalyticsSpendResponse.model_validate(upstream.json())


@router.get("/leases/{lease_id}/analytics/counts/files", response_model=AnalyticsCountsResponse)
def get_analytics_counts_files(
    lease_id: str,
    request: Request,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Forward this lease's chunk's counts-by-file read to the hub over the window."""
    return _counts(lease_id, request, "files", since, until)


@router.get("/leases/{lease_id}/analytics/counts/skills", response_model=AnalyticsCountsResponse)
def get_analytics_counts_skills(
    lease_id: str,
    request: Request,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Forward this lease's chunk's counts-by-skill read to the hub over the window."""
    return _counts(lease_id, request, "skills", since, until)


@router.get("/leases/{lease_id}/analytics/counts/agent-types", response_model=AnalyticsCountsResponse)
def get_analytics_counts_agent_types(
    lease_id: str,
    request: Request,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Forward this lease's chunk's counts-by-agent-type read to the hub over the window."""
    return _counts(lease_id, request, "agent-types", since, until)


@router.get("/leases/{lease_id}/analytics/counts/nodes", response_model=AnalyticsCountsResponse)
def get_analytics_counts_nodes(
    lease_id: str,
    request: Request,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Forward this lease's chunk's counts-by-node read to the hub over the window."""
    return _counts(lease_id, request, "nodes", since, until)


@router.get("/leases/{lease_id}/analytics/spend/nodes", response_model=AnalyticsSpendResponse)
def get_analytics_spend_nodes(
    lease_id: str,
    request: Request,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> AnalyticsSpendResponse:
    """Forward this lease's chunk's spend-by-node read to the hub over the window."""
    return _spend(lease_id, request, "nodes", since, until)


@router.get("/leases/{lease_id}/analytics/spend/graphs", response_model=AnalyticsSpendResponse)
def get_analytics_spend_graphs(
    lease_id: str,
    request: Request,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> AnalyticsSpendResponse:
    """Forward this lease's chunk's spend-by-graph read to the hub over the window."""
    return _spend(lease_id, request, "graphs", since, until)
