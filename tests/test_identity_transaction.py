"""The identity unit of work over a migrated store: every identity write in one block commits together, a block that
raises leaves nothing behind, a read inside the block sees the block's own writes, a tenant-owned write joins the
same transaction through that tenant's builders and lands or rolls back with it, and units of work serialize from their first statement, so a rule one
reads still holds when its writes commit."""

from __future__ import annotations

import shutil
import threading
from contextlib import nullcontext, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog
from sqlalchemy import event

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.hub_admins import SET_BY_BOOTSTRAP, HubAdminFact
from blizzard.hub.auth.identity_transaction import IdentityWrites
from blizzard.hub.auth.internal.identity_transaction import (
    IdentityLockMissing,
    IdentityTransactions,
)
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.memberships import MembershipFact
from blizzard.hub.auth.models import AuthFact, Identity, Session, SuperuserBootstrap, User
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import statements_of
from blizzard.hub.tenancy import StoreScope, TenantId, UnknownTenant
from tests.support import default_tenant, hub_level_connections, hub_migration_prototype, seed_invitation

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
    writes.users.create(User("usr_a", "ada", "Ada", "ada@example.com", _T0))
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


def _accept(writes: IdentityWrites, tenant: TenantId, invitation_id: str) -> None:
    """An invitation's acceptance: the person and their membership, then the tenant-owned fact that it was used."""
    writes.users.create(User("usr_a", "ada", "Ada", "ada@example.com", _T0))
    writes.memberships.record(MembershipFact(tenant, "usr_a", Role.GUEST, _T0, invitation_id))
    with writes.tenant_connections(StoreScope(tenant)).write("accept_invitation") as conn:
        conn.execute(
            statements_of(conn)
            .insert(s.invitation_facts)
            .values(invitation_id=invitation_id, kind="accepted", actor_user_id="usr_a", recorded_at=_T0)
        )


def _accepted(engine: sa.Engine, tenant: TenantId) -> tuple[list[str], list[str], list[str]]:
    with engine.connect() as conn:
        facts = conn.execute(
            sa.select(s.invitation_facts.c.invitation_id).where(s.invitation_facts.c.tenant_id == tenant)
        ).scalars()
        users = conn.execute(sa.select(s.users.c.id)).scalars()
        memberships = conn.execute(sa.select(s.membership_facts.c.set_by)).scalars()
        return list(facts), list(users), list(memberships)


@pytest.mark.parametrize("abandoned", [False, True])
def test_a_tenant_owned_write_commits_or_rolls_back_with_the_identity_writes(
    engine: sa.Engine, abandoned: bool
) -> None:
    tenant = default_tenant(engine)
    invitation_id, _ = seed_invitation(engine, tenant, "ada@example.com", Role.GUEST)

    with pytest.raises(_Abandoned) if abandoned else nullcontext(), _transactions(engine).begin() as writes:
        _accept(writes, tenant, invitation_id)
        if abandoned:
            raise _Abandoned

    written = ([invitation_id], ["usr_a"], [invitation_id])
    assert _accepted(engine, tenant) == (([], [], []) if abandoned else written)


def test_a_tenant_that_does_not_exist_refuses_the_whole_unit(engine: sa.Engine) -> None:
    tenant = default_tenant(engine)
    invitation_id, _ = seed_invitation(engine, tenant, "ada@example.com", Role.GUEST)

    with pytest.raises(UnknownTenant), _transactions(engine).begin() as writes:
        _accept(writes, TenantId("ten_00000000000000000000000000"), invitation_id)

    assert _accepted(engine, tenant) == ([], [], [])


def test_the_identity_lock_is_the_units_first_statement(engine: sa.Engine) -> None:
    statements: list[str] = []

    def record(conn: sa.Connection, cursor: object, statement: str, *args: object) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    with _transactions(engine).begin() as writes:
        writes.hub_admins.holders()
    event.remove(engine, "before_cursor_execute", record)

    assert statements[0].startswith("UPDATE alembic_version SET version_num=")
    assert [statement.split()[0] for statement in statements] == ["UPDATE", "SELECT"]


def test_a_store_with_no_revision_row_writes_no_identity(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(sa.table("alembic_version")))
    before = _counts(engine)

    with pytest.raises(IdentityLockMissing), _transactions(engine).begin() as writes:
        _claim(writes, default_tenant(engine))

    assert _counts(engine) == before


def test_two_units_reading_a_tenants_admins_never_both_act_on_the_count(
    engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each unit steps down as an admin only while another admin remains — the last-admin rule's shape. Held open
    after the read, two units that could both read two admins before either writes are made to."""
    tenant = default_tenant(engine)
    with _transactions(engine).begin() as writes:
        for user_id in ("usr_a", "usr_b"):
            writes.users.create(User(user_id, user_id, user_id, None, _T0))
            writes.memberships.record(MembershipFact(tenant, user_id, Role.ADMIN, _T0, SET_BY_BOOTSTRAP))
    both_read = threading.Barrier(2, timeout=0.5)
    admin_count = MembershipRepository.admin_count

    def held_open(self: MembershipRepository, of: TenantId) -> int:
        count = admin_count(self, of)
        with suppress(threading.BrokenBarrierError):
            both_read.wait()
        return count

    monkeypatch.setattr(MembershipRepository, "admin_count", held_open)
    stepped_down: dict[str, bool] = {}

    def step_down(user_id: str) -> None:
        with _transactions(engine).begin() as writes:
            stepped_down[user_id] = writes.memberships.admin_count(tenant) > 1
            if stepped_down[user_id]:
                writes.memberships.record(MembershipFact(tenant, user_id, Role.CONTRIBUTOR, _T0, user_id))

    threads = [threading.Thread(target=step_down, args=(user_id,)) for user_id in ("usr_a", "usr_b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(stepped_down.values()) == [False, True]
    assert MembershipRepository(hub_level_connections(engine)).admin_count(tenant) == 1
