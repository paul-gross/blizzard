"""The identity unit of work over a migrated store: every identity write in one block commits together, a block that
raises leaves nothing behind, a read inside the block sees the block's own writes, and a tenant-owned write joins the
same transaction through that tenant's builders."""

from __future__ import annotations

import shutil
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.hub_admins import SET_BY_BOOTSTRAP, HubAdminFact
from blizzard.hub.auth.identity_transaction import IdentityWrites
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions, _TransactionConnections
from blizzard.hub.auth.memberships import MembershipFact
from blizzard.hub.auth.models import AuthFact, Identity, Session, SuperuserBootstrap, User
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.scoped import statements_of
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import default_tenant, hub_level_connections, hub_migration_prototype

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_WRITTEN = (
    "users",
    "identities",
    "sessions",
    "membership_facts",
    "hub_admin_facts",
    "superuser_bootstrap",
    "auth_facts",
)


class _Abandoned(Exception):
    pass


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    shutil.copyfile(hub_migration_prototype(), tmp_path / "hub.db")
    return create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")


def _transactions(engine: sa.Engine) -> IdentityTransactions:
    return IdentityTransactions(hub_level_connections(engine), RepoErrorFactory(structlog.get_logger("test")))


def _claim(writes: IdentityWrites, tenant: TenantId) -> None:
    """Every identity table written once, as a first sign-in that claims the hub administrator role does."""
    writes.users.create(User("usr_a", "ada", "Ada", "ada@example.com", Role.SUPERUSER, _T0))
    writes.identities.link(Identity("github", "42", "usr_a", "ada", _T0))
    writes.sessions.create(Session("hash_a", "usr_a", _T0, _T0 + timedelta(days=1), _T0))
    writes.memberships.record(MembershipFact(tenant, "usr_a", Role.ADMIN, _T0, SET_BY_BOOTSTRAP))
    writes.hub_admins.record(HubAdminFact("usr_a", True, _T0, SET_BY_BOOTSTRAP))
    writes.bootstrap.upsert(SuperuserBootstrap("ada@example.com", "usr_a", _T0))
    writes.auth_facts.create(AuthFact("user_role_changed", "system", "usr_a", "pending -> superuser", _T0))


def _counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            name: conn.execute(sa.select(sa.func.count()).select_from(s.metadata.tables[name])).scalar_one()
            for name in _WRITTEN
        }


def test_every_identity_write_in_one_block_commits_together(engine: sa.Engine) -> None:
    tenant = default_tenant(engine)
    with _transactions(engine).begin() as writes:
        _claim(writes, tenant)

    assert _counts(engine) == dict.fromkeys(_WRITTEN, 1)


def test_a_block_that_raises_leaves_nothing_behind(engine: sa.Engine) -> None:
    tenant = default_tenant(engine)
    before = _counts(engine)

    with pytest.raises(_Abandoned), _transactions(engine).begin() as writes:
        _claim(writes, tenant)
        raise _Abandoned

    assert _counts(engine) == before


def test_a_read_inside_the_block_sees_its_own_writes(engine: sa.Engine) -> None:
    tenant = default_tenant(engine)
    with _transactions(engine).begin() as writes:
        _claim(writes, tenant)

        assert writes.users.get("usr_a") is not None
        assert writes.memberships.role_in(tenant, "usr_a") is Role.ADMIN
        assert writes.hub_admins.holders() == ["usr_a"]


@pytest.mark.parametrize("abandoned", [False, True])
def test_a_tenant_owned_write_joins_the_same_transaction(engine: sa.Engine, abandoned: bool) -> None:
    tenant = default_tenant(engine)
    store = hub_level_connections(engine)

    with pytest.raises(_Abandoned) if abandoned else nullcontext(), store.write("accept") as conn:
        pinned = _TransactionConnections(conn, HubStoreErrorFactory(structlog.get_logger("test")))
        with pinned.scoped(StoreScope(tenant)).write("invite") as scoped:
            assert scoped is conn
            scoped.execute(
                statements_of(scoped)
                .insert(s.invitations)
                .values(
                    invitation_id="inv_1",
                    email="ada@example.com",
                    role="guest",
                    token_hash="h",
                    created_by="usr_a",
                    created_at=_T0,
                    expires_at=_T0 + timedelta(days=7),
                )
            )
        with pinned.write("admit") as same:
            same.execute(
                sa.insert(s.membership_facts).values(
                    tenant_id=tenant, user_id="usr_a", role="guest", set_at=_T0, set_by="inv_1"
                )
            )
        if abandoned:
            raise _Abandoned

    with engine.connect() as conn:
        invitations = (
            conn.execute(sa.select(s.invitations.c.invitation_id).where(s.invitations.c.tenant_id == tenant))
            .scalars()
            .all()
        )
        memberships = conn.execute(sa.select(s.membership_facts.c.set_by)).scalars().all()
    assert (invitations, memberships) == (([], []) if abandoned else (["inv_1"], ["inv_1"]))
