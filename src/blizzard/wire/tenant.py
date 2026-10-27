"""Tenant administration and the hub's own event log on the wire — the hub-level ``/api/admin/*`` routes a hub
administrator calls."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints

from blizzard.foundation.tenants import (
    TENANT_NAME_MAX_LENGTH,
    TENANT_NAME_PATTERN,
    TenantDeletionState,
    TenantState,
)
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.graph import GraphSyncEntry
from blizzard.wire.vocabulary import OPEN

#: A tenant's name: non-empty, with no leading or trailing whitespace; inner whitespace, line breaks included, is kept.
TenantName = Annotated[
    str, StringConstraints(min_length=1, max_length=TENANT_NAME_MAX_LENGTH, pattern=TENANT_NAME_PATTERN)
]


class TenantCreateRequest(BaseModel):
    """``POST /api/admin/tenants``: the new tenant's name — a label, never refused for being taken — and, optionally,
    the user to make its first admin."""

    name: TenantName
    admin_user_id: str | None = None


class TenantRenameRequest(BaseModel):
    """``PATCH /api/admin/tenants/{tenant_id}``: the tenant's new name."""

    name: TenantName


class TenantView(WireModel):
    """One tenant: its id, its name, when and by whom it was created, whether it is ``open`` or ``deleting``, and —
    while it is ``deleting`` — when its deletion began."""

    tenant_id: str
    name: str
    created_at: str
    created_by: str
    state: Annotated[TenantState | Unrecognized, OPEN]
    closed_at: str | None = None


class TenantDeletionResponse(WireModel):
    """``DELETE /api/admin/tenants/{tenant_id}``: ``deleted`` with the rows its deletion removed (``200``), or
    ``deleting`` while its deletion is still in progress (``202``)."""

    tenant_id: str
    state: Annotated[TenantDeletionState | Unrecognized, OPEN]
    rows_removed: int | None = None


class TenantGraphSync(WireModel):
    """One tenant's packaged-graph reconciliation: ``ok`` is false iff any of its entries failed."""

    tenant_id: str
    name: str
    ok: bool
    entries: list[GraphSyncEntry] = []


class AdminGraphSyncResponse(WireModel):
    """``POST /api/admin/graphs/sync``'s report — one reconciliation per open tenant. ``ok`` is false iff any tenant's
    is."""

    ok: bool
    tenants: list[TenantGraphSync] = []


class HubEventView(WireModel):
    """One row of the hub's own event log."""

    id: int
    severity: str
    kind: str
    recorded_at: str
    message: str
    detail: dict[str, Any] | None = None
