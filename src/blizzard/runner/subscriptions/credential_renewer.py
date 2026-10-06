"""The per-provider credential-renewal seam — a **pluggable,
provider-selected** external-system seam (``bzh:pluggable-seams``), selected beside each
declared subscription's sampler binding at composition. Renewal is delegated to the
vendor CLI: blizzard never opens a credential file for writing — a binding asks the
vendor's own tooling to refresh it, and reports only whether that ask worked. A read-only due
check and the vendor call are separate, so the caller records its claim between them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.credential_renewal import RenewalFailureReason
from blizzard.foundation.roles import domain_model

__all__ = [
    "RENEWAL_FAILURE_TEXT",
    "ICredentialRenewer",
    "RenewalFailureReason",
    "RenewalOutcome",
    "RenewalOutcomeKind",
    "renewal_due",
]


class RenewalOutcomeKind(StrEnum):
    """One fired renewal's shape. ``RENEWED`` — the vendor CLI refreshed the credential.
    ``FAILED`` — the renewal was attempted but did not succeed; see
    :attr:`RenewalOutcome.failure_reason`."""

    RENEWED = "renewed"
    FAILED = "failed"


# Operator-facing text per closed-set failure reason — what went wrong, not the machine word.
RENEWAL_FAILURE_TEXT: dict[RenewalFailureReason, str] = {
    RenewalFailureReason.RENEWER_UNAVAILABLE: "vendor CLI unavailable",
    RenewalFailureReason.TIMED_OUT: "timed out",
    RenewalFailureReason.VENDOR_REFUSED: "vendor refused",
    RenewalFailureReason.PROTOCOL_ERROR: "unreadable vendor response",
}


@domain_model
@dataclass(frozen=True)
class RenewalOutcome:
    """One ``renew()`` call's result. ``failure_reason`` is set only when
    ``kind`` is :attr:`RenewalOutcomeKind.FAILED`."""

    kind: RenewalOutcomeKind
    failure_reason: RenewalFailureReason | None = None


def renewal_due(expires_at: datetime, now: datetime, lead: timedelta) -> bool:
    """A credential is due for renewal once ``now`` reaches the lead window before its expiry."""
    return now >= expires_at - lead


class ICredentialRenewer(Protocol):
    """One declared subscription's credential-renewal binding. Dumb like the sampler
    seam it stands beside: says whether a renewal is due and renews when asked, never
    decides on a cadence or whether to sample."""

    def renewal_due(self) -> bool:
        """Whether this subscription's credential is at or near its own expiry (this
        binding's own lead window). Read-only and never raises: an unreadable credential is
        not due — that is the sampler's miss to report, not the renewer's."""
        ...

    def renew(self) -> RenewalOutcome:
        """Ask the vendor CLI to refresh this subscription's credential now. Never raises,
        never writes the credential file itself — the vendor's own lock, atomic write, and
        refresh-token rotation stay in force."""
        ...
