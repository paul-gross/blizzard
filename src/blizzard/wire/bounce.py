"""The ``bounce-envelope`` artifact body — the one-line JSON a hub node records when delivery did not complete.

The hub's domain writer (``BounceEnvelope.serialize``) produces it; this is its schema, so the board types the
artifact off the generated client rather than restating the keys."""

from __future__ import annotations

from pydantic import BaseModel


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
    """The ``run:`` step the bounce is attributed to — one that exited non-zero. ``output_tail`` is bounded;
    ``log_artifact`` names the asset holding the full log."""

    name: str
    exit_code: int
    output_tail: str
    log_artifact: str


class BounceEnvelope(BaseModel):
    """Why delivery did not complete: the ``cause``, a readable ``detail``, the repositories not yet landed, and the
    failed ``step`` when a ``run:`` step caused the bounce."""

    cause: str
    detail: str
    unlanded: list[BounceUnlandedRepo]
    step: BounceFailedStep | None
