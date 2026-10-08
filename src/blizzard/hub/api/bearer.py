"""The ``Authorization: Bearer`` header, read in one place (``canon:one-owner``).

What the credential *means* is the caller's: a runner's token on the fleet router (``src/blizzard/hub/api/auth.py``),
a session id on the human plane (``src/blizzard/hub/api/auth_session.py``)."""

from __future__ import annotations

from fastapi import Request

_BEARER_PREFIX = "Bearer "


def presented_bearer(request: Request) -> str | None:
    """The credential presented, or ``None`` with no ``Bearer`` header — an empty one is presented."""
    header = request.headers.get("authorization", "")
    if not header.startswith(_BEARER_PREFIX):
        return None
    return header[len(_BEARER_PREFIX) :]
