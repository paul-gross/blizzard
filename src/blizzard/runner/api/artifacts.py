"""A worker's read of its own node-step artifacts (issue #127), plus its graph mint's own
baked-in declarations — resolved latest-by-epoch for node scope, or one by name, whose
``:path`` converter captures a slash verbatim. Node scope is a hub-proxied forward, since the
worker holds no hub credential of its own; graph scope never reaches the hub, answered
entirely off the runner's own mirror of the pinned mint, read by the lease's ``graph_id``.
Authorization resolves before either source is consulted."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.hub.domain.artifacts import ArtifactScope
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.envelope import EnvelopeArtifact, NodeEnvelope, WorkerArtifact

router = APIRouter(prefix="/api", tags=["runner"])


def _node_row(artifact: EnvelopeArtifact) -> WorkerArtifact:
    return WorkerArtifact(
        scope=ArtifactScope.NODE,
        name=artifact.name,
        kind=artifact.kind,
        node_name=artifact.node_name,
        epoch=artifact.epoch,
        repo=artifact.repo,
        branch_name=artifact.branch_name,
        commit_hash=artifact.commit_hash,
        content=artifact.content,
    )


def _graph_rows(graph_id: str, request: Request) -> list[WorkerArtifact]:
    """This lease's pinned mint's graph-scoped declarations, store-read only — never the hub."""
    reads = RunnerWiring.of(request).reads()
    return [
        WorkerArtifact(scope=ArtifactScope.GRAPH, name=r.name, kind=r.kind, content=r.content)
        for r in reads.graph_artifacts_for_graph(graph_id)
    ]


def _graph_hit(graph_id: str, name: str, request: Request) -> WorkerArtifact | None:
    return next((row for row in _graph_rows(graph_id, request) if row.name == name), None)


def _remaining_levers(*, node: str | None, scope: ArtifactScope | None) -> tuple[str, ...]:
    """The narrowing flags the caller has not already spent. ``node`` names a *producing*
    node, and a graph declaration has none, so supplying it settles the scope too — leaving
    nothing further to narrow with."""
    if node is not None:
        return ()
    return ("--scope", "--node") if scope is None else ("--node",)


def _ambiguous(name: str, candidates: list[WorkerArtifact], *, levers: tuple[str, ...]) -> HTTPException:
    labels = sorted({f"node {c.node_name}" if c.scope is ArtifactScope.NODE else "graph" for c in candidates})
    # Only levers still open to the caller: telling them to pass a flag they already
    # passed is advice they cannot act on.
    hint = f" (pass {' and/or '.join(levers)} to disambiguate)" if levers else ""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"artifact {name!r} is ambiguous — found for: {', '.join(labels)}{hint}",
    )


@dataclass(frozen=True)
class NodeArtifacts:
    """One chunk's envelope artifacts, read through the layered forward to the hub."""

    items: list[EnvelopeArtifact]

    @classmethod
    def of(cls, chunk_id: str, request: Request) -> NodeArtifacts:
        upstream = HubProxy.of(request, "artifacts").get(f"/api/fleet/chunks/{chunk_id}/envelope", chunk_id=chunk_id)
        return cls(NodeEnvelope.model_validate(upstream.json()).artifacts)

    def named(self, name: str, *, node: str | None) -> list[EnvelopeArtifact]:
        matches = [a for a in self.items if a.name == name]
        return matches if node is None else [a for a in matches if a.node_name == node]


@router.get("/leases/{lease_id}/artifacts", response_model=list[WorkerArtifact])
def list_artifacts(lease_id: str, request: Request, scope: ArtifactScope | None = None) -> list[WorkerArtifact]:
    """The worker's own artifacts — every node-step input resolved latest-by-epoch, plus the
    graph mint's own baked-in declarations, both kind-discriminated. ``scope`` narrows to one;
    omitted, both are read. ``scope=graph`` never reaches the hub."""
    lease = authorized_lease(lease_id, request)
    if scope is ArtifactScope.GRAPH:
        return _graph_rows(lease.graph_id, request)
    node_rows = [_node_row(a) for a in NodeArtifacts.of(lease.chunk_id, request).items]
    if scope is ArtifactScope.NODE:
        return node_rows
    return node_rows + _graph_rows(lease.graph_id, request)


@router.get("/leases/{lease_id}/artifacts/{name:path}", response_model=WorkerArtifact)
def get_artifact(
    lease_id: str, name: str, request: Request, node: str | None = None, scope: ArtifactScope | None = None
) -> WorkerArtifact:
    """One artifact by name, optionally narrowed by ``scope`` and, for node scope, by ``node``;
    ``404`` when nothing under the searched scope(s) matches. A supplied ``node`` narrows to node
    scope on its own, a graph declaration having no producing node, so pairing it with
    ``scope=graph`` is ``400``. More than one candidate — several upstream nodes emitting the same
    name (issue #169), or a name in both scopes — is ``409`` naming them, never an arbitrary pick."""
    lease = authorized_lease(lease_id, request)
    if node is not None and scope is ArtifactScope.GRAPH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"--node {node!r} cannot narrow graph scope — a graph's declarations have no producing "
                "node; drop one of --node / --scope graph"
            ),
        )
    if scope is ArtifactScope.GRAPH:
        graph_hit = _graph_hit(lease.graph_id, name, request)
        if graph_hit is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no graph-scoped artifact {name!r} pinned for this lease's mint {lease.graph_id!r}",
            )
        return graph_hit

    node_matches = NodeArtifacts.of(lease.chunk_id, request).named(name, node=node)
    candidates: list[WorkerArtifact] = [_node_row(a) for a in node_matches]
    searched_graph = scope is None and node is None
    if searched_graph:
        graph_hit = _graph_hit(lease.graph_id, name, request)
        if graph_hit is not None:
            candidates.append(graph_hit)
    if not candidates:
        # Names what was actually searched: a graph-scoped miss is only part of the story
        # when graph scope was in the search at all.
        qualifier = f" from node {node!r}" if node is not None else ""
        where = f", nor pinned for its mint {lease.graph_id!r}" if searched_graph else ""
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no artifact {name!r}{qualifier} for this node-step{where}",
        )
    if len(candidates) > 1:
        raise _ambiguous(name, candidates, levers=_remaining_levers(node=node, scope=scope))
    return candidates[0]
