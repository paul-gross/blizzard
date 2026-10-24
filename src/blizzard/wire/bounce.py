"""The ``bounce-envelope`` artifact body — the one-line JSON a hub node records when delivery did not complete.

The hub's domain writer (``BounceEnvelope.serialize``) produces it; this is its schema, so the board types the
artifact off the generated client rather than restating the keys."""

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
    """The ``run:`` step an envelope recorded before hub steps stopped running commands — one that exited non-zero.
    ``output_tail`` is bounded; ``log_artifact`` names the asset holding the full log."""

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
    written ``null``; only an envelope stored before hub steps stopped running commands carries one."""

    cause: str
    detail: str
    unlanded: list[BounceUnlandedRepo]
    step: BounceFailedStep | None = Field(deprecated=True)
    # Defaulted: an envelope stored before the hub named the action carries none, and parses as ``null``.
    action: BounceAction | None = None
