"""``auth.superuser`` bootstrap — ensure/demote/report at hub boot (issue #94).

Idempotent across restarts. Pointing ``auth.superuser`` at a *different* email demotes
whichever user the previous target had claimed (the singleton ``superuser_bootstrap``
row) to ``admin``, so at most one user is ever the bootstrapped superuser. An email
matching no verified user is an unclaimed intent, surfaced at every boot until claimed."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.auth_core import Role
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import SuperuserBootstrap, User
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.users import IReadUserRepository

_log = get_logger("blizzard.hub.auth")


@dataclass(frozen=True)
class Superuser:
    """The configured ``auth.superuser`` intent, reconciled against the hub's own state."""

    email: str | None
    users: IReadUserRepository
    auth: AuthService

    def ensure(self) -> None:
        """Ensure ``email`` holds ``superuser``, demoting a prior different target first —
        the one entry point ``build_hosted_app`` calls."""
        current = self.auth.get_superuser_bootstrap()

        if current is not None and current.email != self.email:
            self._demote(current)
            current = None

        if self.email is None:
            if current is not None:
                self.auth.clear_superuser_bootstrap()
            return

        user = self.users.get_by_email(self.email)
        if user is not None:
            if user.role is not Role.SUPERUSER:
                self.auth.bootstrap_apply_role(user, Role.SUPERUSER)
            self.auth.record_superuser_bootstrap(email=self.email, claimed_user_id=user.user_id)
            return

        # No verified user holds `email` yet — pre-provision (or keep) the unclaimed
        # intent and surface it: every boot while unclaimed, not just the first.
        if current is None or current.claimed_user_id is not None:
            self.auth.record_superuser_bootstrap(email=self.email, claimed_user_id=None)
        _log.warning("superuser bootstrap unclaimed", email=self.email)
        self.auth.report_superuser_bootstrap_unclaimed(email=self.email)

    def _demote(self, previous: SuperuserBootstrap) -> None:
        """Demote the previous target's claimed user to ``admin`` — only when it is still
        ``superuser`` (a manual demotion in between is left alone, not re-applied)."""
        if previous.claimed_user_id is None:
            return
        claimed_user: User | None = self.users.get(previous.claimed_user_id)
        if claimed_user is not None and claimed_user.role is Role.SUPERUSER:
            self.auth.bootstrap_apply_role(claimed_user, Role.ADMIN)
