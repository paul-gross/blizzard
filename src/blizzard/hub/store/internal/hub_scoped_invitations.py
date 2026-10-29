"""The invitation member group of :class:`HubScopedReads` — the lookup an invitation link's token resolves through
before any tenant is known.

The lookup reads the one invitation row holding the token's hash across every tenant and answers its tenant beside its
id; it hydrates nothing. What the invitation admits, and whether it is still live, is read through the resolved
tenant's own invitations, never through here."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import Connection, select

from blizzard.hub.auth.invitations import TenantInvitation
from blizzard.hub.store.schema import invitations
from blizzard.hub.tenancy import TenantId

#: Opens a read, named by its operation, whose every statement is marked hub-scoped
#: (``src/blizzard/hub/store/internal/hub_scoped_reads.py``).
MarkedRead = Callable[[str], AbstractContextManager[Connection]]


class InvitationReads:
    """Resolving an invitation link's token to the invitation and its tenant."""

    def __init__(self, read: MarkedRead) -> None:
        self._read = read

    def invitation_for_token_hash(self, token_hash: str) -> TenantInvitation | None:
        """The invitation whose token hashes to ``token_hash``, or ``None`` when none does."""
        with self._read("resolve an invitation token") as conn:
            row = conn.execute(
                select(invitations.c.tenant_id, invitations.c.invitation_id).where(
                    invitations.c.token_hash == token_hash
                )
            ).first()
        if row is None:
            return None
        return TenantInvitation(tenant=TenantId(row.tenant_id), invitation_id=row.invitation_id)
