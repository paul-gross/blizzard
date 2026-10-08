"""The garden delivery node-step's use case — read the named artifacts, short-circuit a
replay, validate, and materialize (blizzard-product:/delivered/garden/machinery.md
§Delivery). Every decision is `validation.py`'s or `materialize.py`'s; this module only
orders the reads and the write."""

from __future__ import annotations

import functools
from collections.abc import Sequence

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.garden.delivery.materialize import DeliveryOutcome, GardenDelivery, delivery_replay_outcome
from blizzard.hub.domain.garden.delivery.validation import (
    CommitResolver,
    GardenDeliveryRejected,
    select_delta_artifacts,
    select_proposal_artifacts,
    validate_delivery,
)
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import GUARD_ATTEMPTS
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Node


class GardenDeliveryRecorder:
    """Records one delivering node-step's garden delivery. Raises
    :class:`~blizzard.hub.domain.garden.delivery.validation.GardenDeliveryRejected` when an
    artifact is missing or fails validation, nothing written. A finding that moves between validation and
    the write (``GUARD_LOST``) is re-validated against a freshly read bucket, so the result is the same as
    if the mover had landed first; it is refused as rejected once :data:`GUARD_ATTEMPTS` run out."""

    def __init__(
        self,
        *,
        artifacts: IReadChunkArtifactsRepository,
        buckets: FindingBucketReader,
        materialize: GardenDelivery,
        formats: IGardenFormats,
        resolve_commit: CommitResolver | None,
    ) -> None:
        self._artifacts = artifacts
        self._buckets = buckets
        self._materialize = materialize
        self._formats = formats
        self._resolve_commit = resolve_commit

    def record(
        self,
        *,
        chunk: Chunk,
        node: Node,
        epoch: int,
        run: RunContext,
        delta_names: Sequence[str],
        proposal_names: Sequence[str],
    ) -> DeliveryOutcome:
        """Deliver `delta_names`/`proposal_names` for `(chunk, node, epoch)` under `run`.
        The artifacts are selected before the replay check, so a replay whose delta is
        gone is still refused."""
        deltas = select_delta_artifacts(delta_names, self._artifacts.latest_artifacts(chunk.chunk_id, delta_names))
        proposals = select_proposal_artifacts(
            proposal_names, self._artifacts.latest_artifacts(chunk.chunk_id, proposal_names)
        )
        replay = delivery_replay_outcome(
            recorded=self._materialize.already_delivered(chunk_id=chunk.chunk_id, node_id=node.node_id, epoch=epoch)
        )
        if replay is not None:
            return replay
        # One memo across every attempt: a retry never re-resolves a commit, so the delivery spends the
        # fleet-wide hub-exec slot at most once per commit.
        resolve_commit = functools.lru_cache(maxsize=None)(self._resolve_commit) if self._resolve_commit else None
        for _ in range(GUARD_ATTEMPTS):
            validated = validate_delivery(
                run=run,
                delta_artifacts=deltas.contents,
                proposal_artifacts=proposals.contents,
                bucket=self._buckets.for_run(run),
                formats=self._formats,
                resolve_commit=resolve_commit,
            )
            outcome = self._materialize.deliver(
                validated,
                chunk=chunk,
                node=node,
                epoch=epoch,
                delta_artifact_ids=list(deltas.artifact_ids.values()),
                proposal_artifact_ids=[proposals.artifact_ids[name] for name in validated.proposal_sources],
            )
            if outcome is not DeliveryOutcome.GUARD_LOST:
                return outcome
        raise GardenDeliveryRejected(
            "findings this delivery names kept changing while it was being recorded — nothing was written; "
            "re-run the delivery"
        )
