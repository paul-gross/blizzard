"""``auth.superuser`` bootstrap — ensure/demote/report at hub boot (issue #94).

Run once from ``build_hosted_app`` (``hub/app.py``), alongside — and after — the
provider-name-immutability boot check, only once the store is confirmed at the
expected schema head. :func:`ensure_superuser_bootstrap` is the deterministic-shell
orchestrator (``bzh:deterministic-shell``): every store write it performs goes through
:class:`~blizzard.hub.auth.service.AuthService`'s own bootstrap methods
(``bzh:controller-read-only``), never a repository directly.

Idempotent across restarts: a fresh boot with the same ``auth.superuser`` value
re-derives the same outcome every time (promote-if-needed, or report-still-unclaimed).
Changing ``auth.superuser`` to a *different* email demotes whichever user the
*previous* target had claimed (tracked in the singleton ``superuser_bootstrap`` row) to
``admin``, recorded as a ``user_role_changed`` fact — never accumulative: at most one
user is ever the bootstrapped superuser at a time. A configured email matching no
verified user yet is a pre-provisioned, unclaimed intent — claimed later by the first
matching verified login (``AuthService.link_or_mint``) — and is surfaced (logged +
faceted) at *every* boot while it stays unclaimed, never a silent dead end.
"""

from __future__ import annotations

from blizzard.auth_core import Role
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.models import SuperuserBootstrap, User
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.users import IReadUserRepository

_log = get_logger("blizzard.hub.auth")


def ensure_superuser_bootstrap(*, email: str | None, users: IReadUserRepository, auth: AuthService) -> None:
    """Ensure ``email`` (``config.auth.superuser``) holds ``superuser``, demoting a
    prior different target first — the one entry point ``build_hosted_app`` calls."""
    current = auth.get_superuser_bootstrap()

    if current is not None and current.email != email:
        _demote_previous(current, users=users, auth=auth)
        current = None

    if email is None:
        if current is not None:
            auth.clear_superuser_bootstrap()
        return

    user = users.get_by_email(email)
    if user is not None:
        if user.role is not Role.SUPERUSER:
            auth.bootstrap_apply_role(user, Role.SUPERUSER)
        auth.record_superuser_bootstrap(email=email, claimed_user_id=user.user_id)
        return

    # No verified user holds `email` yet — pre-provision (or keep) the unclaimed
    # intent and surface it: every boot while unclaimed, not just the first.
    if current is None or current.claimed_user_id is not None:
        auth.record_superuser_bootstrap(email=email, claimed_user_id=None)
    _log.warning("superuser bootstrap unclaimed", email=email)
    auth.report_superuser_bootstrap_unclaimed(email=email)


def _demote_previous(previous: SuperuserBootstrap, *, users: IReadUserRepository, auth: AuthService) -> None:
    """Demote the previous bootstrap target's claimed user to ``admin`` — only when it
    is still ``superuser`` (a manual demotion in between is left alone, not re-applied)."""
    if previous.claimed_user_id is None:
        return
    claimed_user: User | None = users.get(previous.claimed_user_id)
    if claimed_user is not None and claimed_user.role is Role.SUPERUSER:
        auth.bootstrap_apply_role(claimed_user, Role.ADMIN)
