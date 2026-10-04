"""The subscription sampler's miss vocabulary — one definition, shared by the runner that
records a miss and the wire that reports it."""

from __future__ import annotations

from enum import StrEnum


class SampleMissReason(StrEnum):
    """The closed set of reasons one sampling attempt produced nothing:
    ``CREDENTIAL_LAPSED``, a token past its own expiry or a 401; ``CREDENTIAL_UNREADABLE``, a
    missing, malformed, or incomplete credential file; ``ENDPOINT_UNREACHABLE``, any other
    non-2xx or a request-level failure; ``RESPONSE_UNPARSEABLE``, a 2xx body without windows."""

    CREDENTIAL_LAPSED = "credential_lapsed"
    CREDENTIAL_UNREADABLE = "credential_unreadable"
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    RESPONSE_UNPARSEABLE = "response_unparseable"

    @classmethod
    def recognized(cls, value: object) -> SampleMissReason | None:
        """The member ``value`` names, or None for an absent or unrecognized one — so a stray
        stored value reads as no reason instead of failing a projection."""
        try:
            return cls(value)
        except ValueError:
            return None
