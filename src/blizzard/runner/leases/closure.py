"""The closure-reason vocabulary (``lease_closures.reason``) — the single home every reader and writer names.

Seven reasons are published; the two mint reasons are store-only, recorded on a zero-budget lease minted only
to escalate, and read as ``escalated`` wherever an escalation is derived."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from blizzard.foundation.escalation_causes import EscalationCause

TRANSITIONED = "transitioned"
REAPED = "reaped"
FAILED = "failed"
ESCALATED = "escalated"
#: A runner-config gate: the node-step completed, the chunk parks on a decision.
PARKED = "parked"
#: The chunk was found reassigned, detached or unknown — abandoned, never requeued.
RELEASED = "released"
#: An operator restart re-aimed the chunk; its environments and route are kept.
PREEMPTED = "preempted"

#: The owner-unresolvable escalation mint's own closure reason.
ESCALATION_MINT = "owner-unresolvable-mint"
#: The no-acceptable-harness escalation mint's own closure reason.
NO_ACCEPTABLE_HARNESS_MINT = "no-acceptable-harness-mint"

PUBLISHED_REASONS: frozenset[str] = frozenset({TRANSITIONED, REAPED, FAILED, ESCALATED, PARKED, RELEASED, PREEMPTED})
MINT_REASONS: frozenset[str] = frozenset({ESCALATION_MINT, NO_ACCEPTABLE_HARNESS_MINT})
#: Every reason an open escalation derives from: the ordinary one plus both mints.
ESCALATION_REASONS: frozenset[str] = frozenset({ESCALATED, *MINT_REASONS})

#: The cause each mint reason implies — a mint closure is its own explanation.
_MINT_CAUSES: Mapping[str, str] = MappingProxyType(
    {
        ESCALATION_MINT: EscalationCause.OWNER_UNRESOLVABLE,
        NO_ACCEPTABLE_HARNESS_MINT: EscalationCause.NO_ACCEPTABLE_HARNESS,
    }
)


def cause_of(reason: str, recorded: str | None = None) -> str | None:
    """Why a closure with ``reason`` escalated: the cause a mint reason implies, else the
    cause recorded beside an ordinary ``escalated`` closure, else ``None`` — a closure that
    escalated nothing, or an escalation recorded before its cause was kept."""
    minted = _MINT_CAUSES.get(reason)
    if minted is not None:
        return minted
    return recorded if reason == ESCALATED else None
