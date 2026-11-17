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
    """Why delivery did not complete: the ``cause``, the repositories not yet landed, and the ``action`` whose pass
    answered the bounce — ``null`` when the hub itself judged it, as a poll timeout.

    ``diagnostic`` is the hub's own account of a bounce no action answered: ``null`` is known — there is none — and a
    body that omits the key is unknown, as every body written before it existed. ``detail`` carries the same text
    (``""`` when ``diagnostic`` is ``null``) for readers that predate ``diagnostic``; a body written before it also
    carries a sentence the hub generated from the other fields there.
    """

    cause: str
    detail: str
    unlanded: list[BounceUnlandedRepo]
    # An envelope that names no action parses as ``null``.
    action: BounceAction | None = None
    diagnostic: str | None = None
