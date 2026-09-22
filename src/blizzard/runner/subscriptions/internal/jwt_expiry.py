"""Unverified JWT expiry read, shared by the OpenAI sampler and renewer bindings
(blizzard#504) — one parse, not two, for the same access token's ``exp`` claim."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime


def parse_jwt_expiry(token: str) -> datetime | None:
    """The ``exp`` claim of a JWT, read unverified — the server remains the authority;
    this only lets a caller avoid spending a request on, or wait out, a token already
    dead or about to die. ``None`` for any token this cannot read."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        return None
    try:
        return datetime.fromtimestamp(exp, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
