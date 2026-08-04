"""``blizzard runner takeover`` — wire bodies (issue #52).

Behind ``POST /chunks/{id}/takeovers`` (open) and ``PATCH
/chunks/{id}/takeovers/{tid}`` (end).
"""

from __future__ import annotations

from pydantic import BaseModel


class TakeoverRequest(BaseModel):
    """The takeover request body — ``force`` kills a live worker attempt first."""

    force: bool = False


class TakeoverOpenResponse(BaseModel):
    """``POST /chunks/{id}/takeovers`` — the CLI execs ``command`` verbatim in ``workdir``.

    ``env`` (issue #258) is the bounded takeover env — the lease's ``BLIZZARD_*``
    identity (including the re-minted lease token) plus ``PATH``/``HOME`` — never the
    daemon's full child env (pinned by
    tests/test_runner_takeover.py::test_takeover_env_is_bounded_to_identity_plus_path_and_home).
    It rides only this body; the ``command`` string stays printable-safe.
    """

    takeover_id: str
    command: str
    workdir: str
    env: dict[str, str] = {}


class TakeoverEndResponse(BaseModel):
    """``PATCH /chunks/{id}/takeovers/{tid}`` — the takeover is recorded closed."""

    takeover_id: str
    ended: bool
