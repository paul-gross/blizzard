"""The route claim — how a runner takes work.

``POST /routes`` *is* acquisition: the runner peeks the ready queue, acquires the
chunk's environments from its workspace provider, and posts the **complete** route
— chunk, runner, workspace, and the acquired env ids. The hub accepts exactly one
claim per chunk; a second claim races and loses with **409** (the runner releases
its bindings and moves on). A winning claim's response carries the first node
envelope, so the runner starts working without a second round-trip. A claim from a
runner the hub registry marks paused is refused outright with **403** — a distinct
outcome from the 409 race loss, since this claim was never in the race (issue #44).
"""

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
    """The winning claim's reply — the route plus the first node envelope."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    envelope: NodeEnvelope


class RouteClaimConflict(BaseModel):
    """The 409 body: the claim lost the race; who holds it now."""

    chunk_id: str
    held_by_runner_id: str
    detail: str = "chunk already claimed"


class RouteClaimPausedDenial(BaseModel):
    """The 403 body: the claiming runner is paused at the hub registry (issue #44).

    Distinct from :class:`RouteClaimConflict` — this claim never entered the
    exactly-once race, it was refused outright because the hub's own pause brake
    was already set for ``runner_id``."""

    chunk_id: str
    runner_id: str
    detail: str = "runner is paused at the hub"
