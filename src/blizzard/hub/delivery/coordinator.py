"""The deliver hub-node coordinator — the merge queue.

Delivery is a **hub-executed node**: when a transition lands on it, the hub's
singleton coordinator authors the step's exit transition under a lease and epoch it
mints itself. It never holds code — the chunk's branch artifacts were pushed
to the forge before submission, so the coordinator lands them through the
forge seam (:class:`~blizzard.hub.delivery.forge.IForgeDelivery`).

Within a chunk, repos land **serially, best-effort**: each land is a per-repo
fact, a redelivery skips the already-landed repos (reconciliation), and delivery
completes only when every repo has landed — then a terminal ``delivery.landed`` fact
flips the derived status to ``done`` and the route is released (environments freed,
D-066). A conflict on the unlanded remainder routes intra-graph to the chunk's entry
node in the MVP, partial lands retained for the redelivery reconcile.

The strict-FIFO, one-chunk-at-a-time queue is, in the walking skeleton, the
synchronous single-writer daemon itself: :meth:`deliver` runs to completion for one
chunk before the next apply proceeds. A standing background singleton bolts onto this
same method without reshaping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import TRANSITION_PREFIX, mint
from blizzard.hub.delivery.forge import IForgeDelivery, LandingDisposition, LandingRequest
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import latest_artifacts_by_name
from blizzard.hub.domain.graph import RESERVED_TERMINAL, DeliverMode, Graph, Node
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository

_HUB_RUNNER_ID = "hub"

# Crash points (``bzh:crash-point-registry``) — the hub coordinator's deliver windows.
# Delivery runs synchronously inside the completion-apply request, so an armed point
# SIGKILLs the hub daemon mid-delivery; the runner's completion stays buffered (the POST
# failed) and re-flushes idempotently after the hub restarts, while the per-repo lands
# reconcile. These bracket each per-repo land and the terminal fact.
_CP_DELIVER_BEFORE_REPO = crashpoint("deliver.before-repo-land", "about to land a repo; not yet merged/recorded")
_CP_DELIVER_AFTER_REPO = crashpoint("deliver.after-repo-land", "repo landed and recorded; remainder pending")
_CP_DELIVER_BEFORE_TERMINAL = crashpoint("deliver.before-terminal", "all repos landed; terminal fact not written")
_CP_DELIVER_AFTER_TERMINAL = crashpoint("deliver.after-terminal", "terminal fact + release written; delivery done")


class DeliverOutcome(StrEnum):
    """The terminal outcome of one deliver-node execution."""

    LANDED = "landed"
    CONFLICT = "conflict"
    PR_OPENED = "pr-opened"  # open-pr mode: PRs opened, chunk parked awaiting external merge


@dataclass(frozen=True)
class DeliverResult:
    outcome: DeliverOutcome
    landed_repos: list[str]
    detail: str = ""


class MergeQueueCoordinator:
    """Executes the deliver hub node for one chunk."""

    def __init__(
        self, *, chunks: IWriteChunkRepository, forge: IForgeDelivery, clock: IClock, base_branch: str = "main"
    ) -> None:
        self._chunks = chunks
        self._forge = forge
        self._clock = clock
        # The branch every PR/merge targets. ``main`` matches the verification forge's
        # bare origins; a real GitHub forge whose default branch differs (e.g. ``master``) sets
        # this at the composition root from ``BZ_FORGE_BASE_BRANCH``.
        self._base_branch = base_branch

    def deliver(self, chunk: Chunk, graph: Graph, deliver_node: Node, *, epoch: int) -> DeliverResult:
        """Land the chunk's branch artifacts, per-repo serially, reconciling retries."""
        pointers = [
            row
            for row in latest_artifacts_by_name(self._chunks.load_artifacts(chunk.chunk_id))
            if row.kind is ArtifactKind.GIT_COMMIT
        ]
        if deliver_node.mode == DeliverMode.OPEN_PR:
            return self._open_prs(chunk, deliver_node, epoch=epoch, pointers=pointers)
        already_landed = self._chunks.landed_repos(chunk.chunk_id)

        for row in pointers:
            repo = row.repo or ""
            if repo in already_landed:
                continue  # reconciliation — a prior partial land, skipped
            _CP_DELIVER_BEFORE_REPO.reached()
            result = self._forge.land(_landing_request(row, self._base_branch))
            if result.disposition is LandingDisposition.CONFLICT:
                return self._conflict(chunk, graph, deliver_node, epoch=epoch, detail=result.detail)
            landed_commit = result.landed_commit or _commit_of(row)
            self._chunks.record_delivery_repo_landed(
                chunk.chunk_id, repo=repo, commit_hash=landed_commit, at=self._clock.now()
            )
            _CP_DELIVER_AFTER_REPO.reached()
            already_landed = already_landed | {repo}

        return self._landed(chunk, deliver_node, epoch=epoch, landed=sorted(already_landed))

    def _landed(self, chunk: Chunk, deliver_node: Node, *, epoch: int, landed: list[str]) -> DeliverResult:
        hub_epoch = epoch + 1
        now = self._clock.now()
        _CP_DELIVER_BEFORE_TERMINAL.reached()
        # One atomic, idempotent write: the hub lease, delivery.landed, the terminal
        # transition, and the route release land together, so a mid-delivery
        # ``kill -9`` never leaves the chunk landed-but-not-terminal, and a redelivery
        # after a crash re-enters harmlessly (finalize is a no-op once landed).
        self._chunks.finalize_delivery(
            chunk.chunk_id,
            from_node_id=deliver_node.node_id,
            to_node_id=RESERVED_TERMINAL,
            choice_name=DeliverOutcome.LANDED.value,
            epoch=hub_epoch,
            runner_id=_HUB_RUNNER_ID,
            transition_id=mint(TRANSITION_PREFIX, self._clock),
            at=now,
        )
        _CP_DELIVER_AFTER_TERMINAL.reached()
        return DeliverResult(outcome=DeliverOutcome.LANDED, landed_repos=landed)

    def _open_prs(self, chunk: Chunk, deliver_node: Node, *, epoch: int, pointers: list[ArtifactRow]) -> DeliverResult:
        """Open a PR per repo and **park** the chunk (open-pr mode — D-059).

        The counterpart to the merge path: instead of landing, the coordinator opens a PR
        for each repo's branch and records a ``pr.opened`` fact — writing **no** terminal
        transition and **no** route release, so the chunk derives ``delivering`` (awaiting an
        external merge) with its environments held. A redelivery skips repos that
        already have a ``pr.opened`` fact (reconciliation, mirroring ``landed_repos``); the
        forge's ``open_pr`` additionally reuses an existing PR for the head, closing the
        crash window between the forge create and the fact write. A poll or the on-demand
        ``check-delivery`` route later detects the merge and terminates the chunk.
        """
        already_open = {pr.repo for pr in self._chunks.open_prs(chunk.chunk_id)}
        opened: list[str] = []
        for row in pointers:
            repo = row.repo or ""
            if repo in already_open:
                continue  # reconciliation — a PR was already opened for this repo
            handle = self._forge.open_pr(_landing_request(row, self._base_branch))
            self._chunks.record_pr_opened(
                chunk.chunk_id,
                repo=repo,
                number=handle.number,
                url=handle.url,
                commit_hash=_commit_of(row),
                at=self._clock.now(),
            )
            opened.append(repo)
        return DeliverResult(
            outcome=DeliverOutcome.PR_OPENED, landed_repos=[], detail=f"opened {len(opened)} PR(s), awaiting merge"
        )

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
        # Route retained: the conflict routes back into the runner's warm environments.
        return DeliverResult(
            outcome=DeliverOutcome.CONFLICT,
            landed_repos=sorted(self._chunks.landed_repos(chunk.chunk_id)),
            detail=detail,
        )


def _landing_request(row: ArtifactRow, base_branch: str) -> LandingRequest:
    branch_name, _, commit_hash = row.data.partition(":")
    return LandingRequest(
        repo=row.repo or "", branch_name=branch_name, commit_hash=commit_hash, base_branch=base_branch
    )


def _commit_of(row: ArtifactRow) -> str:
    _, _, commit_hash = row.data.partition(":")
    return commit_hash
