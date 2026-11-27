"""A worker's read of its own node-step artifacts, its graph mint's own
baked-in declarations, and blizzard's own published system-artifact set — node scope
resolved latest-by-epoch unless the read names an epoch or asks for all of them — or one by
name, whose ``:path`` converter captures a slash verbatim. Graph scope answers from the
runner's own pinned-mint mirror; node and system scope proxy through the hub on every call
(``bzh:graph-scope-reads-local``, ``bzh:system-scope-reads-live``). Authorization resolves
before any source is consulted."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.foundation.roles import collaborator
from blizzard.runner.api.hub_proxy import RELAYED_REFUSALS, HubProxy, UpstreamRefusal
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.lifecycle.artifacts import (
    ArtifactAmbiguous,
    ArtifactEpochNotHeld,
    ArtifactNotFound,
    ArtifactRead,
    ArtifactReadContradiction,
    IReadGraphArtifactRepository,
    refuse_epochs_outside_node,
)
from blizzard.wire.chunk import ArtifactView
from blizzard.wire.chunk_artifacts import ChunkArtifactsSource
from blizzard.wire.envelope import EnvelopeArtifact, NodeEnvelope, WorkerArtifact
from blizzard.wire.system_artifact import SYSTEM_ARTIFACT_NOT_FOUND

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


def _series_row(artifact: ArtifactView) -> WorkerArtifact:
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
    graph_artifacts: IReadGraphArtifactRepository = RunnerWiring.of(request).host_read_stores().graph_artifacts
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
    except UpstreamRefusal as exc:
        # A hub before problem details answers the miss with no ``type``; any other typed 404 is not a miss.
        if exc.problem_type in (SYSTEM_ARTIFACT_NOT_FOUND, None) and exc.status_code == status.HTTP_404_NOT_FOUND:
            return None
        raise
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND and not isinstance(exc, UpstreamRefusal):
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


@collaborator
@dataclass(frozen=True)
class NodeArtifactSeries:
    """Every epoch of one chunk's node-scope artifacts, newest first, read through the layered
    forward to the hub's chunk detail."""

    items: list[WorkerArtifact]

    @classmethod
    def of(cls, chunk_id: str, request: Request) -> NodeArtifactSeries:
        upstream = HubProxy.of(request, "artifacts").get(f"/api/fleet/chunks/{chunk_id}", chunk_id=chunk_id)
        served: dict[tuple[str, str, int], ArtifactView] = {}
        for view in ChunkArtifactsSource.model_validate(upstream.json()).artifacts:
            # The first served under a key is the earliest write — the row a bare read serves.
            served.setdefault((view.node_name, view.name, view.epoch), view)
        newest_first = sorted(served.values(), key=lambda v: (v.epoch, v.produced_at or ""), reverse=True)
        return cls([_series_row(view) for view in newest_first])

    def named(self, name: str, *, node: str | None) -> list[WorkerArtifact]:
        matches = [a for a in self.items if a.name == name]
        return matches if node is None else [a for a in matches if a.node_name == node]


@router.get("/leases/{lease_id}/artifacts", response_model=list[WorkerArtifact], responses=RELAYED_REFUSALS)
def list_artifacts(
    lease_id: str, request: Request, scope: ArtifactScope | None = None, all_epochs: bool = False
) -> list[WorkerArtifact]:
    """The worker's own artifacts — every node-step input resolved latest-by-epoch, the graph
    mint's own baked-in declarations, and blizzard's published system-artifact set, all
    kind-discriminated. ``scope`` narrows to one; omitted, all three are read. ``scope=graph``
    never reaches the hub; ``scope=system`` always does. ``all_epochs`` includes each node-scope
    series' superseded epochs, newest first; with ``scope=graph``/``scope=system`` it is ``400``."""
    lease = authorized_lease(lease_id, request)
    if all_epochs:
        try:
            refuse_epochs_outside_node(scope)
        except ArtifactReadContradiction as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope is ArtifactScope.GRAPH:
        return _graph_rows(lease.graph_id, request)
    if scope is ArtifactScope.SYSTEM:
        return _system_rows(request)
    if all_epochs:
        node_rows = NodeArtifactSeries.of(lease.chunk_id, request).items
    else:
        node_rows = [_node_row(a) for a in NodeArtifacts.of(lease.chunk_id, request).items]
    if scope is ArtifactScope.NODE:
        return node_rows
    return node_rows + _graph_rows(lease.graph_id, request) + _system_rows(request)


@router.get("/leases/{lease_id}/artifacts/{name:path}", response_model=WorkerArtifact, responses=RELAYED_REFUSALS)
def get_artifact(
    lease_id: str,
    name: str,
    request: Request,
    node: str | None = None,
    scope: ArtifactScope | None = None,
    epoch: int | None = None,
) -> WorkerArtifact:
    """One artifact by name, optionally narrowed by ``scope`` and, for node scope, by ``node``
    and ``epoch``; ``404`` when nothing matches. A supplied ``node`` or ``epoch`` settles scope to
    node on its own — neither a graph declaration nor a system artifact has a producing node or
    epochs — so pairing either with ``scope=graph``/``scope=system`` is ``400``. A name several
    nodes produced reads as the newest entry (``node`` narrows to one node's series); ``epoch``
    reads that one entry instead, and an epoch the series does not hold is ``404`` naming those it
    holds. ``409`` is a name colliding across scopes, naming them."""
    lease = authorized_lease(lease_id, request)
    read = ArtifactRead(name=name, graph_id=lease.graph_id, node=node, scope=scope, epoch=epoch)
    try:
        read.validate()
    except ArtifactReadContradiction as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    candidates: list[WorkerArtifact] = []
    if read.searches_node():
        if epoch is None:
            candidates += [_node_row(a) for a in NodeArtifacts.of(lease.chunk_id, request).named(name, node=node)]
        else:
            candidates += NodeArtifactSeries.of(lease.chunk_id, request).named(name, node=node)
    if read.searches_graph() and (graph_hit := _graph_hit(lease.graph_id, name, request)) is not None:
        candidates.append(graph_hit)
    if read.searches_system() and (system_hit := _system_hit(name, request)) is not None:
        candidates.append(system_hit)
    try:
        return read.resolve(candidates)
    except (ArtifactNotFound, ArtifactEpochNotHeld) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ArtifactAmbiguous as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
