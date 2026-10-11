"""``auth_facts`` — the append-only non-chunk auth/security event log
(``bzh:facts-not-status``).

The rest of the fact model is chunk-scoped; login, session, and role events are not, so they get their
own durable table rather than being shoehorned onto a chunk. ``SSO_REFUSED`` in particular records a
``state`` presented to a callback for a provider other than the one it was minted for."""

from __future__ import annotations

from typing import Protocol

from blizzard.auth_core import Role
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import AuthFact
from blizzard.hub.tenancy import TenantId

#: A ``state`` failed to resolve at callback — missing, expired, already consumed, or
#: the provider's own code exchange raised.
LOGIN_FAILED = "login_failed"
#: A ``state`` resolved but named a different provider than the callback it was
#: presented to — refused outright rather than treated as a plain expired/missing state.
SSO_REFUSED = "sso_refused"
#: A user's membership in a tenant was granted, changed, or revoked — the acting user
#: (``"system"`` for the superuser claim's own grant), the subject, and the from/to roles
#: and the tenant land in ``detail``.
USER_ROLE_CHANGED = "user_role_changed"
#: A configured ``auth.superuser`` email matches no verified user yet — reported at
#: every boot while unclaimed, never a silent dead end.
SUPERUSER_BOOTSTRAP_UNCLAIMED = "superuser_bootstrap_unclaimed"
#: An acceptance found several accounts holding the invited address in other cases and none holding it exactly, so
#: it chose none of them and resolved the person as a sign-in does — the invitation, the address, and how many
#: accounts hold it.
INVITATION_ADDRESS_AMBIGUOUS = "invitation_address_ambiguous"

_log = get_logger("blizzard.hub.auth")


class IReadAuthFactsRepository(Protocol):
    """Read-only auth-fact lookups."""

    def list_recent(self, *, limit: int = 50) -> list[AuthFact]: ...


class IWriteAuthFactsRepository(IReadAuthFactsRepository, Protocol):
    """Adds the auth-fact writes."""

    def create(self, fact: AuthFact) -> None: ...


class AuthFactsService:
    """Record an :class:`AuthFact`, stamped from the injected clock
    (``bzh:injected-clock``) and logged once at WARNING (``bzh:structlog-logging``) so
    the durable row and the log line never drift apart."""

    def __init__(self, *, facts: IWriteAuthFactsRepository, clock: IClock) -> None:
        self._facts = facts
        self._clock = clock

    def record(
        self,
        kind: str,
        *,
        actor: str,
        subject: str,
        detail: str = "",
        into: IWriteAuthFactsRepository | None = None,
    ) -> None:
        """Record one fact — through ``into`` when it must commit with an identity unit of work's other
        writes, else on its own."""
        fact = AuthFact(kind=kind, actor=actor, subject=subject, detail=detail, recorded_at=self._clock.now())
        (into or self._facts).create(fact)
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

    def user_role_changed(
        self,
        *,
        actor: str,
        subject: str,
        tenant: TenantId,
        from_role: Role | None,
        to_role: Role | None,
        into: IWriteAuthFactsRepository | None = None,
    ) -> None:
        """A membership change in ``tenant``; ``None`` on either side is no membership."""
        detail = f"{_role_text(from_role)} -> {_role_text(to_role)} in {tenant}"
        self.record(USER_ROLE_CHANGED, actor=actor, subject=subject, detail=detail, into=into)

    def superuser_bootstrap_unclaimed(self, *, email: str) -> None:
        self.record(
            SUPERUSER_BOOTSTRAP_UNCLAIMED,
            actor="system",
            subject=email,
            detail="auth.superuser names no verified user yet",
        )

    def invitation_address_ambiguous(
        self, *, invitation_id: str, email: str, holders: int, into: IWriteAuthFactsRepository | None = None
    ) -> None:
        self.record(
            INVITATION_ADDRESS_AMBIGUOUS,
            actor=invitation_id,
            subject=email,
            detail=f"{holders} accounts hold it in other cases, none exactly",
            into=into,
        )


def _role_text(role: Role | None) -> str:
    return "none" if role is None else role.value
