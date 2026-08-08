"""A worker's read of its own node-step artifacts (issue #127) — the whole set resolved latest-by-epoch,
or one by ``produces:`` name, whose ``:path`` converter captures a slash-containing name verbatim.

The worker never holds hub credentials: this route authorizes the lease token minted at its own spawn,
resolves the lease to its ``chunk_id``, and forwards to the hub as the runner principal. Nothing is
persisted or cached, and authorization resolves before the hub is consulted."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.envelope import EnvelopeArtifact, NodeEnvelope

router = APIRouter(prefix="/api", tags=["runner"])


@dataclass(frozen=True)
class Artifacts:
    """One chunk's envelope artifacts, read through the layered forward to the hub."""

    items: list[EnvelopeArtifact]

    @classmethod
    def of(cls, chunk_id: str, request: Request) -> Artifacts:
        upstream = HubProxy.of(request, "artifacts").get(f"/api/fleet/chunks/{chunk_id}/envelope", chunk_id=chunk_id)
        return cls(NodeEnvelope.model_validate(upstream.json()).artifacts)

    def named(self, name: str, *, node: str | None) -> list[EnvelopeArtifact]:
        matches = [a for a in self.items if a.name == name]
        return matches if node is None else [a for a in matches if a.node_name == node]


@router.get("/leases/{lease_id}/artifacts", response_model=list[EnvelopeArtifact])
def list_artifacts(lease_id: str, request: Request) -> list[EnvelopeArtifact]:
    """The worker's own node-step inputs — every artifact resolved latest-by-epoch,
    both kinds, kind-discriminated."""
    lease = authorized_lease(lease_id, request)
    return Artifacts.of(lease.chunk_id, request).items


@router.get("/leases/{lease_id}/artifacts/{name:path}", response_model=EnvelopeArtifact)
def get_artifact(lease_id: str, name: str, request: Request, node: str | None = None) -> EnvelopeArtifact:
    """One artifact by ``produces:`` name, optionally narrowed by ``node``; ``404`` when this node-step
    has none by that name. More than one upstream node can emit the same name (issue #169), so a bare
    name resolving to several candidates is ``409`` naming them, never an arbitrary pick."""
    lease = authorized_lease(lease_id, request)
    matches = Artifacts.of(lease.chunk_id, request).named(name, node=node)
    if not matches:
        qualifier = f" from node {node!r}" if node is not None else ""
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no artifact {name!r}{qualifier} for this node-step"
        )
    if len(matches) > 1:
        candidates = sorted({a.node_name for a in matches})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"artifact {name!r} is ambiguous — produced by nodes: {', '.join(candidates)} "
            "(pass --node to disambiguate)",
        )
    return matches[0]
