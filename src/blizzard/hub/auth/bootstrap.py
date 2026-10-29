"""``auth.superuser`` bootstrap — record and report the configured target at hub boot.

Boot promotes nobody: the configured email is recorded in the singleton ``superuser_bootstrap`` row as an intent,
which a sign-in claims under the rules ``src/blizzard/hub/auth/service.py`` owns (:meth:`AuthService.link_or_mint`).
Pointing ``auth.superuser`` at a different email records a new, unclaimed intent. Unsetting it cancels an intent nobody
has claimed and leaves a claimed one alone. An unclaimed intent is surfaced at every boot until claimed."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.service import AuthService

_log = get_logger("blizzard.hub.auth")


@dataclass(frozen=True)
class Superuser:
    """The configured ``auth.superuser`` intent, reconciled against the hub's own state."""

    email: str | None
    auth: AuthService

    def ensure(self) -> None:
        """Record ``email`` as the claim target when it is new, and report it while unclaimed;
        with no email configured, cancel an unclaimed target."""
        if self.email is None:
            self.auth.cancel_unclaimed_superuser_bootstrap()
            return
        current = self.auth.get_superuser_bootstrap()
        if current is None or current.email != self.email:
            self.auth.record_superuser_bootstrap(email=self.email, claimed_user_id=None)
        elif current.claimed_user_id is not None:
            return
        _log.warning("superuser bootstrap unclaimed", email=self.email)
        self.auth.report_superuser_bootstrap_unclaimed(email=self.email)
