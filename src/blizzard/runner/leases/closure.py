"""The store-only closure reasons (``lease_closures.reason``) and the reason sets derived from them.

The published reasons are :class:`blizzard.foundation.leases.LeaseClosureReason`. The two mint reasons here are
store-only, recorded on a zero-budget lease minted only to escalate; the version-refused reason is store-only too,
recorded on a lease whose launch a changed harness version refused. All three spend no retry, and read as
``escalated`` wherever an escalation is derived or a close reason published."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.leases import LeaseClosureReason

#: The owner-unresolvable escalation mint's own closure reason.
ESCALATION_MINT = "owner-unresolvable-mint"
#: The no-acceptable-harness escalation mint's own closure reason.
NO_ACCEPTABLE_HARNESS_MINT = "no-acceptable-harness-mint"
#: The closure reason of a lease escalated because a changed harness version refused its launch, relaunch included.
HARNESS_VERSION_REFUSED = "harness-version-refused"

PUBLISHED_REASONS: frozenset[str] = frozenset(LeaseClosureReason)
MINT_REASONS: frozenset[str] = frozenset({ESCALATION_MINT, NO_ACCEPTABLE_HARNESS_MINT})
#: The store-only reasons that each record an escalation: both mints and the version-refused close.
STORE_ESCALATION_REASONS: frozenset[str] = frozenset({*MINT_REASONS, HARNESS_VERSION_REFUSED})
#: The reasons that close a completed node-step; the newest one at a node ends its visit and its retry budget.
COMPLETED_STEP_REASONS: frozenset[str] = frozenset({LeaseClosureReason.TRANSITIONED, LeaseClosureReason.PARKED})
#: The reasons whose attempt spends no retry: superseded, let go, a mint that never spawned, or a launch refused.
UNSPENT_REASONS: frozenset[str] = frozenset(
    {LeaseClosureReason.PREEMPTED, LeaseClosureReason.RELEASED, *STORE_ESCALATION_REASONS}
)
#: The reasons whose attempt spends a retry.
SPENT_REASONS: frozenset[str] = frozenset(
    {LeaseClosureReason.FAILED, LeaseClosureReason.REAPED, LeaseClosureReason.ESCALATED}
)
#: Every reason an open escalation derives from: the ordinary one plus every store-only one.
ESCALATION_REASONS: frozenset[str] = frozenset({LeaseClosureReason.ESCALATED, *STORE_ESCALATION_REASONS})

#: The cause each store-only escalation reason implies — such a closure is its own explanation.
_STORE_CAUSES: Mapping[str, str] = MappingProxyType(
    {
        ESCALATION_MINT: EscalationCause.OWNER_UNRESOLVABLE,
        NO_ACCEPTABLE_HARNESS_MINT: EscalationCause.NO_ACCEPTABLE_HARNESS,
        HARNESS_VERSION_REFUSED: EscalationCause.HARNESS_VERSION_CHANGED,
    }
)


def cause_of(reason: str, recorded: str | None = None) -> str | None:
    """Why a closure with ``reason`` escalated: the cause a store-only reason implies, else the
    cause recorded beside an ordinary ``escalated`` closure, else ``None`` — a closure that
    escalated nothing, or an escalation recorded before its cause was kept."""
    minted = _STORE_CAUSES.get(reason)
    if minted is not None:
        return minted
    return recorded if reason == LeaseClosureReason.ESCALATED else None
