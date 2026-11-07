"""The route claim — how a runner takes work.

``POST /routes`` *is* acquisition: the claimant posts the **complete** route — chunk, workspace, and the acquired env
ids — and its bearer token names the runner. Exactly one claim per chunk is accepted; a second races and loses with
**409**."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.envelope import NodeEnvelope
from blizzard.wire.problem import ProblemDetails, ProblemMembers, StatuslessProblemMembers
from blizzard.wire.vocabulary import OPEN


class RouteClaim(BaseModel):
    """A complete route fact posted by the claiming runner. The claimant is the runner its bearer
    token names; the body carries no runner id."""

    chunk_id: str
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
    ``incompatible_runner_id`` field. ``reason`` says what the runner lacks: the capabilities, or the chunk's
    project among those it serves — then ``detail`` names the project."""

    chunk_id: str
    incompatible_runner_id: str
    detail: str = "runner capabilities no longer satisfy the chunk's reachable lineage"
    reason: Annotated[ClaimDenialReason | Unrecognized, OPEN] = ClaimDenialReason.CAPABILITY


class RouteClaimPausedDenial(BaseModel):
    """The 403 body: the claiming runner itself is refused — paused at the hub registry,
    retired, or not registered there at all — and ``detail`` names which.

    Distinct from a claim conflict — this claim never entered the race."""

    chunk_id: str
    runner_id: str
    detail: str = "runner is paused at the hub"


class RouteClaimConflictProblem(
    StatuslessProblemMembers[Literal["urn:blizzard:problem:claim-conflict"]], RouteClaimConflict
):
    """A lost claim race; status is deliberately absent."""

    chunk_id: str = Field(deprecated=True)
    held_by_runner_id: str = Field(deprecated=True)


class RouteClaimTerminalProblem(
    ProblemMembers[Literal["urn:blizzard:problem:claim-terminal"]], RouteClaimTerminalDenial
):
    """A claim denial whose legacy status is a chunk status, not HTTP status."""

    chunk_id: str = Field(deprecated=True)
    status: str = Field(deprecated=True)


class RouteClaimNotReadyProblem(
    ProblemMembers[Literal["urn:blizzard:problem:claim-not-ready"]], RouteClaimTerminalDenial
):
    """A claim denied for now, retaining the released chunk-status string."""

    chunk_id: str = Field(deprecated=True)
    status: str = Field(deprecated=True)


class RouteClaimDependencyProblem(
    StatuslessProblemMembers[Literal["urn:blizzard:problem:claim-dependency"]], RouteClaimDependencyDenial
):
    """An unmet prerequisite; status is deliberately absent."""

    chunk_id: str = Field(deprecated=True)
    prerequisite_chunk_id: str = Field(deprecated=True)


class RouteClaimIncompatibleProblem(
    StatuslessProblemMembers[Literal["urn:blizzard:problem:claim-incompatible"]], RouteClaimIncompatibleDenial
):
    """A runner unable to execute a claim; status is deliberately absent."""

    chunk_id: str = Field(deprecated=True)
    incompatible_runner_id: str = Field(deprecated=True)
    reason: Annotated[ClaimDenialReason | Unrecognized, OPEN] = Field(
        default=ClaimDenialReason.CAPABILITY, deprecated=True
    )


class RouteClaimPausedProblem(ProblemDetails[Literal["urn:blizzard:problem:claim-paused"]], RouteClaimPausedDenial):
    """A runner barred from claiming."""

    chunk_id: str = Field(deprecated=True)
    runner_id: str = Field(deprecated=True)


class ClaimRunnerRetiredProblem(
    ProblemDetails[Literal["urn:blizzard:problem:claim-runner-retired"]], RouteClaimPausedDenial
):
    """A claim from a retired runner."""

    chunk_id: str = Field(deprecated=True)
    runner_id: str = Field(deprecated=True)


class ClaimRunnerUnregisteredProblem(
    ProblemDetails[Literal["urn:blizzard:problem:claim-runner-unregistered"]], RouteClaimPausedDenial
):
    """A claim from a runner not registered at the hub."""

    chunk_id: str = Field(deprecated=True)
    runner_id: str = Field(deprecated=True)


class ClaimRunnerVersionRefusedProblem(
    ProblemDetails[Literal["urn:blizzard:problem:claim-runner-version-refused"]], RouteClaimPausedDenial
):
    """A claim from a runner refused by the hub's version window."""

    chunk_id: str = Field(deprecated=True)
    runner_id: str = Field(deprecated=True)


class PinnedGraphMissingProblem(ProblemDetails[Literal["urn:blizzard:problem:pinned-graph-missing"]]):
    """The chunk's pinned graph is missing."""


class MalformedCursorProblem(ProblemDetails[Literal["urn:blizzard:problem:malformed-cursor"]]):
    """The supplied pagination cursor is malformed."""


class RouteNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:route-not-found"]]):
    """The chunk has no live route."""


class RouteNotHeldProblem(ProblemDetails[Literal["urn:blizzard:problem:route-not-held"]]):
    """The caller does not hold the route or its capability is invalid."""


class RouteTerminalProblem(ProblemDetails[Literal["urn:blizzard:problem:route-terminal"]]):
    """The route's chunk has ended and cannot be rekeyed."""


class RouteTokenRekeyResponse(BaseModel):
    """A fresh plaintext route capability token for the chunk's live route,
    returned exactly once here."""

    chunk_id: str
    route_token: str
