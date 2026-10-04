"""The garden-proposal vocabulary the wire carries — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class GardenProposalOrigin(StrEnum):
    """Who authored a garden proposal — a mint-time fact, stored on
    the row itself and never inferred from a null `routine_name`."""

    ROUTINE_RUN = "routine-run"
    OPERATOR = "operator"


class GardenProposalClosureKind(StrEnum):
    """How a garden proposal closed — recorded on the row itself when it closes, never
    derived from anything else."""

    PASSED = "passed"
    ACCEPTED = "accepted"


class GardenProposalItemOutcome(StrEnum):
    """Whether an accepted proposal minted a work item — recorded positively rather than
    inferred from an absent link, so a declined mint reads as a decision, not a gap."""

    MINTED = "minted"
    DECLINED = "declined"
