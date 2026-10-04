"""The door a configured write arrived through, read from ``X-Blizzard-Door``.

Only the doors a client really is — ``cli`` and ``board`` — are honoured. ``apply`` and
``migration`` are set server-side, never by a caller, so they and every absent or unknown
value record ``api``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.config.changes import ChangeContext, Door

DOOR_HEADER = "X-Blizzard-Door"


def request_door(raw: str | None) -> Door:
    return Door.claimed((raw or "").strip().lower())


def request_door_of(x_blizzard_door: Annotated[str | None, Header()] = None) -> Door:
    """A dependency: the door the request's ``X-Blizzard-Door`` header names."""
    return request_door(x_blizzard_door)


def change_context(identity: ResolvedIdentity, door: Door) -> ChangeContext:
    """Who is writing — the authenticated identity, never a body field — and through which door."""
    return ChangeContext(actor=identity.user_id, door=door)


RequestDoor = Annotated[Door, Depends(request_door_of)]
