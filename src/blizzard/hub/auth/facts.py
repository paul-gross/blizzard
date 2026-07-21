"""``auth_facts`` — the append-only non-chunk auth/security event log (issue #92,
``bzh:facts-not-status``).

The hub's existing fact model is chunk-scoped; login/session/role events are not, so
they get their own small durable table rather than being shoehorned onto a chunk. Two
kinds land in this phase: ``LOGIN_FAILED`` (a bad/expired ``state`` or a failed
provider code exchange) and ``SSO_REFUSED`` (a ``state`` presented to a callback for a
provider other than the one it was minted for — a cross-provider replay/tamper
attempt). ``USER_ROLE_CHANGED`` is #94's; not declared here.
"""

from __future__ import annotations

from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import AuthFact

#: A ``state`` failed to resolve at callback — missing, expired, already consumed, or
#: the provider's own code exchange raised.
LOGIN_FAILED = "login_failed"
#: A ``state`` resolved but named a different provider than the callback it was
#: presented to — refused outright rather than treated as a plain expired/missing state.
SSO_REFUSED = "sso_refused"

_log = get_logger("blizzard.hub.auth")


class IReadAuthFactsRepository(Protocol):
    """Read-only auth-fact lookups."""

    def list_recent(self, *, limit: int = 50) -> list[AuthFact]: ...


class IWriteAuthFactsRepository(IReadAuthFactsRepository, Protocol):
    """Read-write auth-fact access — only the domain layer depends on this variant."""

    def create(self, fact: AuthFact) -> None: ...


class AuthFactsService:
    """Record an :class:`AuthFact`, stamped from the injected clock
    (``bzh:injected-clock``) and logged once at WARNING (``bzh:structlog-logging``) so
    the durable row and the log line never drift apart."""

    def __init__(self, *, facts: IWriteAuthFactsRepository, clock: IClock) -> None:
        self._facts = facts
        self._clock = clock

    def record(self, kind: str, *, actor: str, subject: str, detail: str = "") -> None:
        fact = AuthFact(kind=kind, actor=actor, subject=subject, detail=detail, recorded_at=self._clock.now())
        self._facts.create(fact)
        _log.warning("auth fact recorded", kind=kind, actor=actor, subject=subject, detail=detail)

    def list_recent(self, *, limit: int = 50) -> list[AuthFact]:
        """The read passthrough a test (or a future admin surface) inspects the
        durable log through, rather than reaching past the service into its
        repository (``bzh:controller-read-only``)."""
        return self._facts.list_recent(limit=limit)

    def login_failed(self, *, actor: str, subject: str, detail: str = "") -> None:
        self.record(LOGIN_FAILED, actor=actor, subject=subject, detail=detail)

    def sso_refused(self, *, actor: str, subject: str, detail: str = "") -> None:
        self.record(SSO_REFUSED, actor=actor, subject=subject, detail=detail)
