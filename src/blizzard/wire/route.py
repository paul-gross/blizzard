"""The route claim — how a runner takes work.

``POST /routes`` *is* acquisition: the claimant posts the **complete** route — chunk,
runner, workspace, and the acquired env ids. Exactly one claim per chunk is accepted; a
second races and loses with **409**, and an unregistered, retired, or paused claimant is
refused with **403**."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.envelope import NodeEnvelope


class RouteClaim(BaseModel):
    """A complete route fact posted by the claiming runner."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]


class RouteClaimResponse(BaseModel):
    """The winning claim's reply — the route, its first node envelope, and the
    route's plaintext capability token, returned exactly once here."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    envelope: NodeEnvelope
    route_token: str


class RouteClaimConflict(BaseModel):
    """The 409 body: the claim lost the race; who holds it now."""

    chunk_id: str
    held_by_runner_id: str
    detail: str = "chunk already claimed"


class RouteClaimTerminalDenial(BaseModel):
    """The status-carrying 409 body: the chunk stands at a status no claim is legal from — refused
    outright, not a race loss or claim conflict. ``status`` tells the cases apart: an ended chunk
    (``done``, ``stopped``) can never be claimed again, while one not ready (``not_ready``, an unclaimed
    ``paused``, or a human-gated or delivering chunk with no route) may become claimable later."""

    chunk_id: str
    status: str
    detail: str = "chunk is terminal"


class RouteClaimDependencyDenial(BaseModel):
    """The 409 body: the chunk stands on a prerequisite that has not reached ``done``
    — refused outright, not a race loss. Distinct from a claim conflict
    and a terminal chunk: no other runner holds it and it is not terminal, it simply named
    a prerequisite still standing."""

    chunk_id: str
    prerequisite_chunk_id: str
    detail: str = "chunk depends on an unmet prerequisite"


class RouteClaimIncompatibleDenial(BaseModel):
    """The 409 body: the claiming runner's stored capabilities can no longer run every
    statically reachable runner-owned lineage from the chunk's current node
    — refused outright, not a race loss. Distinguished by its own
    ``incompatible_runner_id`` field."""

    chunk_id: str
    incompatible_runner_id: str
    detail: str = "runner capabilities no longer satisfy the chunk's reachable lineage"


class RouteClaimPausedDenial(BaseModel):
    """The 403 body: the claiming runner itself is refused — paused at the hub registry,
    retired, or not registered there at all — and ``detail`` names which.

    Distinct from a claim conflict — this claim never entered the race."""

    chunk_id: str
    runner_id: str
    detail: str = "runner is paused at the hub"


class RouteTokenRekeyResponse(BaseModel):
    """A fresh plaintext route capability token for the chunk's live route,
    returned exactly once here."""

    chunk_id: str
    route_token: str
