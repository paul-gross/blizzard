"""The subscription sampler's miss vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class SampleMissReason(StrEnum):
    """The closed set of reasons one sampling attempt produced nothing:
    ``credential_lapsed``, a token past its own expiry or a 401; ``credential_unreadable``, a
    missing, malformed, or incomplete credential file; ``endpoint_unreachable``, any other
    non-2xx or a request-level failure; ``response_unparseable``, a 2xx body without windows."""

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
