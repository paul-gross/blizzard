"""``GET /api/me`` wire models — the resolved identity and the tenants it can enter."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from blizzard.auth_core import Permission, Role
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class MeTenant(BaseModel):
    """One tenant the caller holds a membership in: its id (the value an
    ``X-Blizzard-Tenant`` header names), its name, the membership's role, and every
    permission the caller holds there."""

    tenant_id: str
    name: str
    role: Annotated[Role | Unrecognized, OPEN]
    permissions: list[Annotated[Permission | Unrecognized, OPEN]]


class MeResponse(WireModel):
    """The resolved identity's wire view. ``role`` and ``permissions`` are those of the tenant
    the request resolves — the one ``X-Blizzard-Tenant`` names among ``tenants``, else the
    caller's only membership. When the request resolves no tenant ``role`` is null and ``permissions`` holds
    only the hub-level permissions of a hub administrator."""

    user_id: str
    username: str
    display_name: str
    role: Annotated[Role | Unrecognized, OPEN] | None
    permissions: list[Annotated[Permission | Unrecognized, OPEN]]
    #: Every role the role-assignment API may grant.
    assignable_roles: list[Annotated[Role | Unrecognized, OPEN]] = []
    #: Every open tenant the caller holds a membership in, ordered by id.
    tenants: list[MeTenant] = []
    #: Whether the caller is a hub administrator.
    hub_admin: bool = False
