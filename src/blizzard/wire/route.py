"""The route claim — how a runner takes work.

``POST /routes`` *is* acquisition: the claimant posts the **complete** route — chunk,
runner, workspace, and the acquired env ids. Exactly one claim per chunk is accepted; a
second races and loses with **409**, and a paused claimant is refused with **403**."""

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
    route's plaintext capability token (issue #84a), returned exactly once here."""

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
    """The 409 body: the chunk is already terminal ({done, stopped}) — refused outright,
    not a race loss (issue #118). Distinct from a claim conflict: no other runner holds
    this chunk, it simply can never be claimed again."""

    chunk_id: str
    status: str
    detail: str = "chunk is terminal"


class RouteClaimPausedDenial(BaseModel):
    """The 403 body: the claiming runner is paused at the hub registry (issue #44).

    Distinct from a claim conflict — this claim never entered the race."""

    chunk_id: str
    runner_id: str
    detail: str = "runner is paused at the hub"


class RouteTokenRekeyResponse(BaseModel):
    """A fresh plaintext route capability token for the chunk's live route (issue #84b),
    returned exactly once here."""

    chunk_id: str
    route_token: str
