"""The deliver hub-node coordinator (D-030/D-079/D-091) — the merge queue.

Delivery is a **hub-executed node**: when a transition lands on it, the hub's
singleton coordinator authors the step's exit transition under a lease and epoch it
mints itself (D-079). It never holds code — the chunk's branch artifacts were pushed
to the forge before submission (D-026), so the coordinator lands them through the
forge seam (:class:`~blizzard.hub.delivery.forge.IForgeDelivery`).

Within a chunk, repos land **serially, best-effort** (D-091): each land is a per-repo
fact, a redelivery skips the already-landed repos (reconciliation), and delivery
completes only when every repo has landed — then a terminal ``delivery.landed`` fact
flips the derived status to ``done`` and the route is released (environments freed,
D-066). A conflict on the unlanded remainder routes intra-graph to the chunk's entry
node in the MVP (D-086), partial lands retained for the redelivery reconcile.

The strict-FIFO, one-chunk-at-a-time queue (D-057) is, in the walking skeleton, the
synchronous single-writer daemon itself: :meth:`deliver` runs to completion for one
chunk before the next apply proceeds. A standing background singleton bolts onto this
same method without reshaping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import TRANSITION_PREFIX, mint
from blizzard.hub.delivery.forge import IForgeDelivery, LandingDisposition, LandingRequest
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import latest_artifacts_by_name
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Graph, Node
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository

_HUB_RUNNER_ID = "hub"


class DeliverOutcome(StrEnum):
    """The terminal outcome of one deliver-node execution (D-086)."""

    LANDED = "landed"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class DeliverResult:
    outcome: DeliverOutcome
    landed_repos: list[str]
    detail: str = ""


class MergeQueueCoordinator:
    """Executes the deliver hub node for one chunk (D-030/D-091)."""

    def __init__(self, *, chunks: IWriteChunkRepository, forge: IForgeDelivery, clock: IClock) -> None:
        self._chunks = chunks
        self._forge = forge
        self._clock = clock

    def deliver(self, chunk: Chunk, graph: Graph, deliver_node: Node, *, epoch: int) -> DeliverResult:
        """Land the chunk's branch artifacts, per-repo serially, reconciling retries."""
        pointers = [
            row
            for row in latest_artifacts_by_name(self._chunks.load_artifacts(chunk.chunk_id))
            if row.kind is ArtifactKind.GIT_COMMIT
        ]
        already_landed = self._chunks.landed_repos(chunk.chunk_id)

        for row in pointers:
            repo = row.repo or ""
            if repo in already_landed:
                continue  # reconciliation — a prior partial land, skipped (D-091)
            result = self._forge.land(_landing_request(row))
            if result.disposition is LandingDisposition.CONFLICT:
                return self._conflict(chunk, graph, deliver_node, epoch=epoch, detail=result.detail)
            landed_commit = result.landed_commit or _commit_of(row)
            self._chunks.record_delivery_repo_landed(
                chunk.chunk_id, repo=repo, commit_hash=landed_commit, at=self._clock.now()
            )
            already_landed = already_landed | {repo}

        return self._landed(chunk, deliver_node, epoch=epoch, landed=sorted(already_landed))

    def _landed(self, chunk: Chunk, deliver_node: Node, *, epoch: int, landed: list[str]) -> DeliverResult:
        hub_epoch = epoch + 1
        now = self._clock.now()
        self._chunks.record_lease(chunk.chunk_id, epoch=hub_epoch, runner_id=_HUB_RUNNER_ID, at=now)
        self._chunks.record_delivery_landed(chunk.chunk_id, at=now)
        self._chunks.record_transition(
            transition_id=mint(TRANSITION_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            from_node_id=deliver_node.node_id,
            to_node_id=RESERVED_TERMINAL,
            choice_name=DeliverOutcome.LANDED.value,
            epoch=hub_epoch,
            runner_id=_HUB_RUNNER_ID,
            at=now,
            artifacts=[],
        )
        # Terminal outcome reported — the holding runner's environments are freed (D-066).
        self._chunks.record_route_released(chunk.chunk_id, at=now)
        return DeliverResult(outcome=DeliverOutcome.LANDED, landed_repos=landed)

    def _conflict(self, chunk: Chunk, graph: Graph, deliver_node: Node, *, epoch: int, detail: str) -> DeliverResult:
        hub_epoch = epoch + 1
        now = self._clock.now()
        self._chunks.record_lease(chunk.chunk_id, epoch=hub_epoch, runner_id=_HUB_RUNNER_ID, at=now)
        self._chunks.record_transition(
            transition_id=mint(TRANSITION_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            from_node_id=deliver_node.node_id,
            to_node_id=graph.entry_node_id,
            choice_name=DeliverOutcome.CONFLICT.value,
            epoch=hub_epoch,
            runner_id=_HUB_RUNNER_ID,
            at=now,
            artifacts=[],
        )
        # Route retained: the conflict routes back into the runner's warm environments (D-066).
        return DeliverResult(
            outcome=DeliverOutcome.CONFLICT,
            landed_repos=sorted(self._chunks.landed_repos(chunk.chunk_id)),
            detail=detail,
        )


def _landing_request(row: ArtifactRow) -> LandingRequest:
    branch_name, _, commit_hash = row.data.partition(":")
    return LandingRequest(repo=row.repo or "", branch_name=branch_name, commit_hash=commit_hash)


def _commit_of(row: ArtifactRow) -> str:
    _, _, commit_hash = row.data.partition(":")
    return commit_hash
