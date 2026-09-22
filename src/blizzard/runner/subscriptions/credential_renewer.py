"""The per-provider credential-renewal seam (blizzard#504) — a **pluggable,
provider-selected** external-system seam (``bzh:pluggable-seams``), selected beside each
declared subscription's sampler binding at composition. Renewal is delegated to the
vendor CLI: blizzard never opens a credential file for writing (D1) — a binding asks the
vendor's own tooling to refresh it, and reports only whether that ask worked."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = ["ICredentialRenewer", "RenewalFailureReason", "RenewalOutcome", "RenewalOutcomeKind"]


class RenewalOutcomeKind(StrEnum):
    """One renewal attempt's shape. ``RENEWED`` — the vendor CLI refreshed the
    credential. ``NOT_DUE`` — the credential is not at or near its own expiry, or this
    binding could not tell (an unreadable credential is the sampler's own miss to
    report, not the renewer's). ``FAILED`` — renewal was due and attempted, but did not
    succeed; see :attr:`RenewalOutcome.failure_reason`."""

    RENEWED = "renewed"
    NOT_DUE = "not_due"
    FAILED = "failed"


class RenewalFailureReason(StrEnum):
    """The closed set of reasons a due renewal attempt did not succeed.

    ``RENEWER_UNAVAILABLE`` is the vendor CLI missing or unrunnable (e.g. not on
    ``PATH``); ``TIMED_OUT`` is the bounded subprocess exceeding its timeout;
    ``VENDOR_REFUSED`` is the vendor CLI exiting non-zero or its own response reporting
    it could not refresh; ``PROTOCOL_ERROR`` is a response this binding could not make
    sense of at all."""

    RENEWER_UNAVAILABLE = "renewer_unavailable"
    TIMED_OUT = "timed_out"
    VENDOR_REFUSED = "vendor_refused"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(frozen=True)
class RenewalOutcome:
    """One ``renew_if_due()`` call's result. ``failure_reason`` is set only when
    ``kind`` is :attr:`RenewalOutcomeKind.FAILED`."""

    kind: RenewalOutcomeKind
    failure_reason: RenewalFailureReason | None = None


class ICredentialRenewer(Protocol):
    """One declared subscription's credential-renewal binding. Dumb like the sampler
    seam it stands beside: renews when due, never decides whether to sample."""

    def renew_if_due(self) -> RenewalOutcome:
        """Ask the vendor CLI to refresh this subscription's credential, but only when
        it is at or near its own expiry (this binding's own lead window). Never raises,
        never writes the credential file itself — the vendor's own lock, atomic write,
        and refresh-token rotation stay in force."""
        ...
