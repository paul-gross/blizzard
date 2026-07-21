"""Reading a worker's presented lease token off a request (issue #113, Phase 2;
issue #127).

The one place the two accepted forms are decoded — the dedicated
``X-Blizzard-Lease-Token`` header, falling back to a standard ``Authorization:
Bearer`` — so the write edge (``attachments.py``) and the read edge (``artifacts.py``)
authorize a worker off the exact same rule rather than two copies that could drift.
The decoded token is handed to :func:`~blizzard.runner.domain.lease_auth.check_lease_token`;
this module only pulls it from the wire.
"""

from __future__ import annotations

from fastapi import Request

__all__ = ["presented_lease_token"]

_BEARER_PREFIX = "Bearer "


def presented_lease_token(request: Request) -> str | None:
    """The lease token off ``X-Blizzard-Lease-Token``, falling back to a standard
    ``Authorization: Bearer`` header — either form is accepted, the dedicated header
    checked first. ``None`` when neither is present."""
    dedicated = request.headers.get("x-blizzard-lease-token")
    if dedicated:
        return dedicated
    authorization = request.headers.get("authorization", "")
    if authorization.startswith(_BEARER_PREFIX):
        return authorization[len(_BEARER_PREFIX) :]
    return None
