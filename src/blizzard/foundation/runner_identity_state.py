"""A runner's identity-verdict vocabulary."""

from __future__ import annotations

from enum import StrEnum


class IdentityState(StrEnum):
    """Whether this process may work as the configured runner: its token's own verdict, and — on a registered
    host — whether the hub holds it under the host."""

    #: Not yet verified since the process started.
    UNCHECKED = "unchecked"
    #: The token resolves to the configured runner, and nothing refuses it.
    MATCHED = "matched"
    #: The token resolves to a different runner.
    MISMATCHED = "mismatched"
    #: The hub refuses the token: unknown, revoked, or its runner retired.
    REFUSED = "refused"
    #: The host's hub does not hold this runner under the host.
    NOT_HELD = "not_held"
    #: The runner stands in another tenant than the host's.
    TENANT_DIFFERS = "tenant_differs"
    #: The hub's version window refuses the runner.
    VERSION_REFUSED = "version_refused"
