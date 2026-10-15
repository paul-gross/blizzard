"""A runner's identity-verdict vocabulary — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class IdentityState(StrEnum):
    """Whether this process may work as the configured runner: its token's own verdict, and — on a registered
    host — whether the hub holds it under the host."""

    #: Not yet verified since the process started: every hub call answers as during an outage.
    UNCHECKED = "unchecked"
    #: The token resolves to the configured runner, and nothing refuses it.
    MATCHED = "matched"
    #: The token resolves to a different runner: the runner refuses to work until it matches again.
    MISMATCHED = "mismatched"
    #: The hub refuses the token — unknown, revoked, or its runner retired: the runner refuses to work until a
    #: check resolves it again.
    REFUSED = "refused"
    #: The host's hub answers that it does not hold this runner under the host: the runner halts alone.
    NOT_HELD = "not_held"
    #: The runner stands in another tenant than the host's: the runner halts alone.
    TENANT_DIFFERS = "tenant_differs"
