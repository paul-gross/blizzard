"""The credential-renewal outcome vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class RenewalFailureReason(StrEnum):
    """The closed set of reasons a due renewal attempt did not succeed: ``renewer_unavailable``,
    no renewer could be run; ``timed_out``, the attempt overran its bound; ``vendor_refused``,
    the vendor declined to refresh; ``protocol_error``, the vendor's answer was unintelligible."""

    RENEWER_UNAVAILABLE = "renewer_unavailable"
    TIMED_OUT = "timed_out"
    VENDOR_REFUSED = "vendor_refused"
    PROTOCOL_ERROR = "protocol_error"

    @classmethod
    def recognized(cls, value: object) -> RenewalFailureReason | None:
        """The member ``value`` names, or None for an absent or unrecognized one — so a stray
        stored value reads as no reason instead of failing a projection."""
        try:
            return cls(value)
        except ValueError:
            return None


class RenewalResult(StrEnum):
    """What one renewal attempt is known to have come to. ``RENEWED`` and ``FAILED`` are a
    recorded outcome; ``UNRECORDED`` is an attempt with no outcome on record — still in flight,
    or its outcome lost to a crash or a failed write."""

    RENEWED = "renewed"
    FAILED = "failed"
    UNRECORDED = "unrecorded"

    @classmethod
    def recognized(cls, value: object) -> RenewalResult | None:
        """The member ``value`` names, or None for an absent or unrecognized one."""
        try:
            return cls(value)
        except ValueError:
            return None
