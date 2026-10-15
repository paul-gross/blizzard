"""A runner host's registration vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class HostRegistrationState(StrEnum):
    """Where the host stands with its hub, as its latest report found it."""

    #: The host holds no host token: it reports nothing, and each runner signs in and reports on its own.
    UNREGISTERED = "unregistered"
    #: The host holds a host token and has not reported yet.
    PENDING = "pending"
    #: The hub answered the latest report with the host's own identity.
    REGISTERED = "registered"
    #: The hub refuses the host's token, or answered as another host or tenant than the one recorded.
    REFUSED = "refused"
    #: The hub answered 404: it predates hosts.
    NO_HOSTS = "no_hosts"
    #: The latest report did not reach the hub, or it did not answer.
    UNREACHABLE = "unreachable"
