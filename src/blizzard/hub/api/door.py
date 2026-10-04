"""The door a configured write arrived through, read from ``X-Blizzard-Door``.

Only the doors a client really is — ``cli`` and ``board`` — are honoured. ``apply`` and
``migration`` are set server-side, never by a caller, so they and every absent or unknown
value record ``api``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header

from blizzard.auth_core import Permission
from blizzard.hub.api.auth_session import require
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.config.changes import ChangeContext, Door

DOOR_HEADER = "X-Blizzard-Door"

_CLIENT_DOORS = {Door.CLI.value: Door.CLI, Door.BOARD.value: Door.BOARD}


def request_door(raw: str | None) -> Door:
    return _CLIENT_DOORS.get((raw or "").strip().lower(), Door.API)


def request_context(permission: Permission) -> Callable[..., ChangeContext]:
    """A dependency factory: gate on ``permission``, then name the actor (the authenticated
    identity, never a body field) and the door."""

    # Bound as a default, not an ``Annotated`` metadata: the module's string annotations
    # resolve against module globals, which cannot see ``permission``.
    gate = Depends(require(permission))

    def _dependency(
        identity: ResolvedIdentity = gate,
        x_blizzard_door: Annotated[str | None, Header()] = None,
    ) -> ChangeContext:
        return ChangeContext(actor=identity.user_id, door=request_door(x_blizzard_door))

    return _dependency
