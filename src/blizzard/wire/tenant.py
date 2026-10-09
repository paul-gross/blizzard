"""Tenant administration and the hub's own event log on the wire — the hub-level ``/api/admin/*`` routes a hub
administrator calls."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from blizzard.wire.graph import GraphSyncEntry


class TenantCreateRequest(BaseModel):
    """``POST /api/admin/tenants``: the new tenant's name — a label, never refused for being taken — and, optionally,
    the user to make its first admin."""

    name: str = Field(min_length=1)
    admin_user_id: str | None = None


class TenantRenameRequest(BaseModel):
    """``PATCH /api/admin/tenants/{tenant_id}``: the tenant's new name."""

    name: str = Field(min_length=1)


class TenantView(BaseModel):
    """One tenant: its id, its name, when and by whom it was created, and whether it is ``open`` or ``deleting``."""

    tenant_id: str
    name: str
    created_at: str
    created_by: str
    state: Literal["open", "deleting"]


class TenantGraphSync(BaseModel):
    """One tenant's packaged-graph reconciliation: ``ok`` is false iff any of its entries failed."""

    tenant_id: str
    name: str
    ok: bool
    entries: list[GraphSyncEntry] = []


class AdminGraphSyncResponse(BaseModel):
    """``POST /api/admin/graphs/sync``'s report — one reconciliation per open tenant. ``ok`` is false iff any tenant's
    is."""

    ok: bool
    tenants: list[TenantGraphSync] = []


class HubEventView(BaseModel):
    """One row of the hub's own event log."""

    id: int
    severity: str
    kind: str
    recorded_at: str
    message: str
    detail: dict[str, Any] | None = None
