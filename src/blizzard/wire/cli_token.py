"""``POST /api/auth/cli/token``'s answer — the hub session token a CLI login redeems."""

from __future__ import annotations

from pydantic import BaseModel


class CliTokenResponse(BaseModel):
    """The minted hub session token — never a runner-style JWT."""

    token: str
