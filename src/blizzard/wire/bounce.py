"""The ``bounce-envelope`` artifact body and the shapes it nests: the unlanded repositories and their pull requests,
and the answering action."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.base import WireModel


class BouncePullRequest(BaseModel):
    """The pull request an unlanded repository's branch is open or closed under."""

    number: int
    url: str


class BounceUnlandedRepo(BaseModel):
    """A repository whose branch had not landed when delivery bounced; ``pr`` is ``null`` where none was raised."""

    repo: str
    branch: str
    commit: str
    pr: BouncePullRequest | None


class BounceAction(BaseModel):
    """The hub action whose pass answered the ``choice`` the bounce routed; ``log_artifact`` names its
    ``hub-log.<action>`` asset, ``null`` when the pass logged nothing."""

    name: str
    choice: str
    log_artifact: str | None


class BounceEnvelope(WireModel):
    """Why delivery did not complete: the ``cause``, a readable ``detail``, the repositories not yet landed, and the
    ``action`` whose pass answered the bounce — ``null`` when the hub itself judged it, as a poll timeout."""

    cause: str
    detail: str
    unlanded: list[BounceUnlandedRepo]
    # An envelope that names no action parses as ``null``.
    action: BounceAction | None = None
