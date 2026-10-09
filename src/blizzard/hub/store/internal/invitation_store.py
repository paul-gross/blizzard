"""SQLAlchemy adapter for one tenant's invitations (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain sees only
:mod:`blizzard.hub.auth.invitations`' shapes. Each invitation is read beside the one ``invitation_facts`` row that
ended it, if any — an outer join, so a live invitation keeps its row."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Row, Select

from blizzard.auth_core import Role
from blizzard.hub.auth.invitations import (
    Invitation,
    InvitationOutcome,
    InvitationOutcomeKind,
    InvitationRecord,
    IWriteInvitationRepository,
)
from blizzard.hub.store.schema import invitation_facts, invitations
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId


class InvitationStore:
    """Read-write invitation adapter over one tenant's connections."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    # --- reads ----------------------------------------------------------

    def get(self, invitation_id: str) -> InvitationRecord | None:
        with self._store.read("get invitation") as conn:
            row = conn.execute(self._records().where(invitations.c.invitation_id == invitation_id)).one_or_none()
        return _record(row) if row is not None else None

    def list(self) -> list[InvitationRecord]:
        with self._store.read("list invitations") as conn:
            rows = conn.execute(self._records().order_by(invitations.c.invitation_id)).all()
        return [_record(row) for row in rows]

    # --- writes ---------------------------------------------------------

    def create(self, invitation: Invitation) -> None:
        with self._store.write("create invitation") as conn:
            conn.execute(
                self._store.insert(invitations).values(
                    invitation_id=invitation.invitation_id,
                    email=invitation.email,
                    role=invitation.role.value,
                    token_hash=invitation.token_hash,
                    created_by=invitation.created_by,
                    created_at=invitation.created_at,
                    expires_at=invitation.expires_at,
                )
            )

    def record(self, outcome: InvitationOutcome) -> None:
        with self._store.write("record invitation outcome") as conn:
            conn.execute(
                self._store.insert(invitation_facts).values(
                    invitation_id=outcome.invitation_id,
                    kind=outcome.kind.value,
                    actor_user_id=outcome.actor_user_id,
                    recorded_at=outcome.recorded_at,
                )
            )

    # --- helpers --------------------------------------------------------

    def _records(self) -> Select[Any]:
        ended = self._store.scope_join(
            invitations, invitation_facts, invitation_facts.c.invitation_id == invitations.c.invitation_id, isouter=True
        )
        return self._store.select(
            ended,
            *invitations.c,
            invitation_facts.c.kind,
            invitation_facts.c.actor_user_id,
            invitation_facts.c.recorded_at,
        )


def _record(row: Row[Any]) -> InvitationRecord:
    invitation = Invitation(
        invitation_id=row.invitation_id,
        tenant=TenantId(row.tenant_id),
        email=row.email,
        role=Role(row.role),
        token_hash=row.token_hash,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )
    outcome = (
        None
        if row.kind is None
        else InvitationOutcome(
            invitation_id=row.invitation_id,
            kind=InvitationOutcomeKind(row.kind),
            actor_user_id=row.actor_user_id,
            recorded_at=row.recorded_at,
        )
    )
    return InvitationRecord(invitation, outcome)


def _conforms_invitation_store(x: InvitationStore) -> IWriteInvitationRepository:
    return x
