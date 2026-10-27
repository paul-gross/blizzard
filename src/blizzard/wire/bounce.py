"""The ``bounce-envelope`` artifact body and the shapes it nests: the unlanded repositories and their pull requests,
the failed step, and the answering action."""

from __future__ import annotations

from pydantic import BaseModel, Field

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


class BounceFailedStep(BaseModel):
    """A step that exited non-zero. ``output_tail`` is bounded; ``log_artifact`` names the asset holding the full
    log."""

    name: str
    exit_code: int
    output_tail: str
    log_artifact: str


class BounceAction(BaseModel):
    """The hub action whose pass answered the ``choice`` the bounce routed; ``log_artifact`` names its
    ``hub-log.<action>`` asset, ``null`` when the pass logged nothing."""

    name: str
    choice: str
    log_artifact: str | None


class BounceEnvelope(WireModel):
    """Why delivery did not complete: the ``cause``, a readable ``detail``, the repositories not yet landed, and the
    ``action`` whose pass answered the bounce — ``null`` when the hub itself judged it, as a poll timeout. ``step`` is
    deprecated and ``null`` unless the envelope carries a failed step."""

    cause: str
    detail: str
    unlanded: list[BounceUnlandedRepo]
    step: BounceFailedStep | None = Field(deprecated=True)
    # An envelope that names no action parses as ``null``.
    action: BounceAction | None = None
