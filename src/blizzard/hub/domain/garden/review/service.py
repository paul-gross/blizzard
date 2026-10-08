"""The review-findings delivery node-step's use case — short-circuit a replay, read the
chunk's review-finding delta, validate, and materialize. Every decision is
`src/blizzard/hub/domain/garden/review/validation.py`'s or
`src/blizzard/hub/domain/garden/review/materialize.py`'s; this module only orders the reads and the write."""

from __future__ import annotations

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.review.materialize import (
    ReviewFindingsMaterialize,
    ReviewFindingsOutcome,
    review_replay_outcome,
)
from blizzard.hub.domain.garden.review.validation import (
    REVIEW_FINDING_DELTA_ARTIFACT,
    require_review_delta,
    validate_review_findings,
)
from blizzard.hub.domain.graph.model import Node


class ReviewFindingsRecorder:
    """Records a chunk's review-findings delivery. Raises
    :class:`~blizzard.hub.domain.garden.review.validation.ReviewFindingsRejected` when the
    chunk carries no review-finding delta or it fails validation, nothing written."""

    def __init__(
        self,
        *,
        artifacts: IReadChunkArtifactsRepository,
        materialize: ReviewFindingsMaterialize,
        formats: IGardenFormats,
    ) -> None:
        self._artifacts = artifacts
        self._materialize = materialize
        self._formats = formats

    def record(self, *, chunk: Chunk, node: Node, epoch: int) -> ReviewFindingsOutcome:
        """Deliver `chunk`'s newest review-finding delta from `(node, epoch)`. The replay
        check comes first, so a recorded chunk answers ``ALREADY_RECORDED`` without
        re-reading its artifact."""
        replay = review_replay_outcome(recorded=self._materialize.already_delivered(chunk_id=chunk.chunk_id))
        if replay is not None:
            return replay
        artifact = require_review_delta(
            self._artifacts.latest_artifact(chunk.chunk_id, REVIEW_FINDING_DELTA_ARTIFACT), chunk_id=chunk.chunk_id
        )
        validated = validate_review_findings(self._formats.review_delta(REVIEW_FINDING_DELTA_ARTIFACT, artifact.data))
        return self._materialize.deliver(validated, chunk=chunk, node=node, epoch=epoch)
