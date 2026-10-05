"""A worker's read of its own node-step artifacts, its graph mint's own
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

from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.foundation.roles import collaborator
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.lifecycle.judgement.artifacts import (
    ArtifactAmbiguous,
    ArtifactNotFound,
    ArtifactRead,
    ArtifactReadContradiction,
    IReadGraphArtifactRepository,
)
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
    graph_artifacts: IReadGraphArtifactRepository = RunnerWiring.of(request).read_stores().graph_artifacts
    return [
        WorkerArtifact(scope=ArtifactScope.GRAPH, name=r.name, kind=r.kind, content=r.content)
        for r in graph_artifacts.graph_artifacts_for_graph(graph_id)
    ]


def _graph_hit(graph_id: str, name: str, request: Request) -> WorkerArtifact | None:
    return next((row for row in _graph_rows(graph_id, request) if row.name == name), None)


def _system_rows(request: Request) -> list[WorkerArtifact]:
    """The published system-artifact set (``ArtifactScope.SYSTEM``) — a hub-proxied forward
    on every call, never a runner-local answer (``bzh:system-scope-reads-live``)."""
    upstream = HubProxy.of(request, "artifacts").get("/api/fleet/system-artifacts")
    return [
        WorkerArtifact(scope=ArtifactScope.SYSTEM, name=item["name"], kind=ArtifactKind.ASSET, content=item["content"])
        for item in upstream.json()
    ]


def _system_hit(name: str, request: Request) -> WorkerArtifact | None:
    """One system artifact by name, or ``None`` on a genuine miss — any other upstream
    failure (unreachable, non-404 status) propagates rather than reading as "not found"."""
    proxy = HubProxy.of(request, "artifacts")
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


@collaborator
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
    nodes, or a name colliding across scopes — is ``409`` naming them."""
    lease = authorized_lease(lease_id, request)
    read = ArtifactRead(name=name, graph_id=lease.graph_id, node=node, scope=scope)
    try:
        read.validate()
    except ArtifactReadContradiction as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    candidates: list[WorkerArtifact] = []
    if read.searches_node():
        candidates += [_node_row(a) for a in NodeArtifacts.of(lease.chunk_id, request).named(name, node=node)]
    if read.searches_graph() and (graph_hit := _graph_hit(lease.graph_id, name, request)) is not None:
        candidates.append(graph_hit)
    if read.searches_system() and (system_hit := _system_hit(name, request)) is not None:
        candidates.append(system_hit)
    try:
        return read.resolve(candidates)
    except ArtifactNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactAmbiguous as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
