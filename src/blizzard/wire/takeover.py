"""``blizzard runner takeover`` — wire bodies (issue #52).

Behind ``POST /chunks/{id}/takeovers`` (open) and ``PATCH
/chunks/{id}/takeovers/{tid}`` (end). Modeled on ``wire/runner_status.py``'s shapes.
"""

from __future__ import annotations

from pydantic import BaseModel


class TakeoverRequest(BaseModel):
    """The takeover request body — ``force`` kills a live worker attempt first."""

    force: bool = False


class TakeoverOpenResponse(BaseModel):
    """``POST /chunks/{id}/takeovers`` — the CLI execs ``command`` verbatim in ``workdir``."""

    takeover_id: str
    command: str
    workdir: str


class TakeoverEndResponse(BaseModel):
    """``PATCH /chunks/{id}/takeovers/{tid}`` — the CLI calls this once its child exits."""

    takeover_id: str
    ended: bool
