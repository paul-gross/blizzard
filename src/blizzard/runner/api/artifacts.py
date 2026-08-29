"""A worker's read of its own node-step artifacts (issue #127), its graph mint's own
baked-in declarations, and blizzard's own published system-artifact set — resolved
latest-by-epoch for node scope, or one by name, whose ``:path`` converter captures a slash
verbatim. Graph scope answers from the runner's own pinned-mint mirror; node and system
scope proxy through the hub on every call (``bzh:graph-scope-reads-local``,
``bzh:system-scope-reads-live``). Authorization resolves before any source is consulted."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactScope
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.envelope import EnvelopeArtifact, NodeEnvelope, WorkerArtifact

router = APIRouter(prefix="/api", tags=["runner"])

#: Scopes that name no producing node — pairing either with ``--node`` is a contradiction.
_NODELESS_SCOPES = (ArtifactScope.GRAPH, ArtifactScope.SYSTEM)


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


def _system_rows(request: Request) -> list[WorkerArtifact]:
    """The published system-artifact set (``ArtifactScope.SYSTEM``) — a hub-proxied forward
    on every call, never a runner-local answer (``bzh:system-scope-reads-live``)."""
    upstream = HubProxy.of(request, "system-artifacts").get("/api/fleet/system-artifacts")
    return [
        WorkerArtifact(scope=ArtifactScope.SYSTEM, name=item["name"], kind=ArtifactKind.ASSET, content=item["content"])
        for item in upstream.json()
    ]


def _system_hit(name: str, request: Request) -> WorkerArtifact | None:
    """One system artifact by name, or ``None`` on a genuine miss — any other upstream
    failure (unreachable, non-404 status) propagates rather than reading as "not found"."""
    proxy = HubProxy.of(request, "system-artifacts")
    try:
        upstream = proxy.get(f"/api/fleet/system-artifacts/{quote(name, safe='/')}")
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            return None
        raise
    body = upstream.json()
    return WorkerArtifact(
        scope=ArtifactScope.SYSTEM, name=body["name"], kind=ArtifactKind.ASSET, content=body["content"]
    )


def _remaining_levers(*, node: str | None, scope: ArtifactScope | None) -> tuple[str, ...]:
    """The narrowing flags the caller has not already spent. ``node`` names a *producing*
    node, and neither a graph declaration nor a system artifact has one, so supplying it
    settles the scope too — leaving nothing further to narrow with."""
    if node is not None:
        return ()
    return ("--scope", "--node") if scope is None else ("--node",)


def _ambiguous(name: str, candidates: list[WorkerArtifact], *, levers: tuple[str, ...]) -> HTTPException:
    def _label(c: WorkerArtifact) -> str:
        if c.scope is ArtifactScope.NODE:
            return f"node {c.node_name}"
        return "system" if c.scope is ArtifactScope.SYSTEM else "graph"

    labels = sorted({_label(c) for c in candidates})
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
    """The worker's own artifacts — every node-step input resolved latest-by-epoch, the graph
    mint's own baked-in declarations, and blizzard's published system-artifact set, all
    kind-discriminated. ``scope`` narrows to one; omitted, all three are read. ``scope=graph``
    never reaches the hub; ``scope=system`` always does."""
    lease = authorized_lease(lease_id, request)
    if scope is ArtifactScope.GRAPH:
        return _graph_rows(lease.graph_id, request)
    if scope is ArtifactScope.SYSTEM:
        return _system_rows(request)
    node_rows = [_node_row(a) for a in NodeArtifacts.of(lease.chunk_id, request).items]
    if scope is ArtifactScope.NODE:
        return node_rows
    return node_rows + _graph_rows(lease.graph_id, request) + _system_rows(request)


@router.get("/leases/{lease_id}/artifacts/{name:path}", response_model=WorkerArtifact)
def get_artifact(
    lease_id: str, name: str, request: Request, node: str | None = None, scope: ArtifactScope | None = None
) -> WorkerArtifact:
    """One artifact by name, optionally narrowed by ``scope`` and, for node scope, by ``node``;
    ``404`` when nothing matches. A supplied ``node`` settles scope to node on its own — neither
    a graph declaration nor a system artifact has a producing node — so pairing it with
    ``scope=graph``/``scope=system`` is ``400``. More than one candidate — several upstream
    nodes (issue #169), or a name colliding across scopes — is ``409`` naming them."""
    lease = authorized_lease(lease_id, request)
    if node is not None and scope is not None and scope in _NODELESS_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"--node {node!r} cannot narrow {scope.value} scope — only node scope has a producing "
                f"node; drop one of --node / --scope {scope.value}"
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
    if scope is ArtifactScope.SYSTEM:
        system_hit = _system_hit(name, request)
        if system_hit is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no system artifact {name!r}")
        return system_hit

    node_matches = NodeArtifacts.of(lease.chunk_id, request).named(name, node=node)
    candidates: list[WorkerArtifact] = [_node_row(a) for a in node_matches]
    searched_other_scopes = scope is None and node is None
    if searched_other_scopes:
        graph_hit = _graph_hit(lease.graph_id, name, request)
        if graph_hit is not None:
            candidates.append(graph_hit)
        system_hit = _system_hit(name, request)
        if system_hit is not None:
            candidates.append(system_hit)
    if not candidates:
        # Names what was actually searched: a graph/system miss is only part of the story
        # when those scopes were in the search at all.
        qualifier = f" from node {node!r}" if node is not None else ""
        where = (
            f", nor pinned for its mint {lease.graph_id!r}, nor a published system artifact"
            if (searched_other_scopes)
            else ""
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no artifact {name!r}{qualifier} for this node-step{where}",
        )
    if len(candidates) > 1:
        raise _ambiguous(name, candidates, levers=_remaining_levers(node=node, scope=scope))
    return candidates[0]
