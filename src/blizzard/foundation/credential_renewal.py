"""The credential-renewal outcome vocabulary — one definition, shared by the runner that
records a renewal and the wire that reports it."""

from __future__ import annotations

from enum import StrEnum


class RenewalFailureReason(StrEnum):
    """The closed set of reasons a due renewal attempt did not succeed: ``RENEWER_UNAVAILABLE``,
    the vendor CLI missing or unrunnable; ``TIMED_OUT``, the bounded subprocess overrunning its
    timeout; ``VENDOR_REFUSED``, a non-zero exit or a response saying it could not refresh;
    ``PROTOCOL_ERROR``, a response this binding could not make sense of at all."""

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
