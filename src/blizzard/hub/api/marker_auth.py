"""The marker-write route's own authorization dependency.

A second way past that route's gate, layered in front of the human
``require(Permission.CHUNK_CONTROL)`` rather than replacing it: a marker token the request's tenant
was resolved by (:func:`~blizzard.hub.api.auth_session.resolve_tenant` verifies it, in every auth
mode, against this request's exact ``(chunk_id, node_id, epoch)`` triple) grants without a session,
else it falls through.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from blizzard.auth_core import Permission
from blizzard.hub.api.auth_session import MARKER_TOKEN_HEADER, TenantRequest, require, resolve_tenant
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.delivery.marker_auth import MarkerPrincipal

#: The header the ``record-marker`` command presents its token in.
_MARKER_TOKEN_HEADER = MARKER_TOKEN_HEADER

#: The human gate this dependency falls through to, built once and shared.
_require_chunk_control = require(Permission.CHUNK_CONTROL)


def require_marker_authority(
    request: Request, tenant: Annotated[TenantRequest, Depends(resolve_tenant)]
) -> ResolvedIdentity | MarkerPrincipal:
    """Grant on a live marker token for this exact node-step, else defer to the human gate."""
    if isinstance(tenant.identity, MarkerPrincipal):
        return tenant.identity
    return _require_chunk_control(request, tenant)
