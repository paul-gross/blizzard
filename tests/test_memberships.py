"""Memberships as facts over a migrated store: the newest fact per ``(tenant, user)`` is in force, a fact with no role
revokes, a person's memberships span tenants while each tenant's members stay its own, and a membership's role is
only ``guest``, ``contributor`` or ``admin``."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.memberships import (
    SET_BY_BOOTSTRAP,
    SET_BY_MIGRATION,
    SET_BY_OPERATOR,
    Membership,
    MembershipFact,
    MembershipRoleRefused,
)
from blizzard.hub.store import schema as s
from blizzard.hub.tenancy import TenantId
from tests.support import default_tenant, hub_level_connections, hub_migration_prototype

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_OTHER = TenantId("ten_01J9Z3M0P8QK7V2S4W6X8Y0A1C")


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    shutil.copyfile(hub_migration_prototype(), tmp_path / "hub.db")
    return create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")


@pytest.fixture
def tenants(engine: sa.Engine) -> tuple[TenantId, TenantId]:
    first = default_tenant(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(s.tenants).values(tenant_id=_OTHER, name="other", created_at=_T0, created_by="usr_a"))
    return first, _OTHER


def _fact(tenant: TenantId, user_id: str, role: Role | None, *, minute: int, by: str = "usr_a") -> MembershipFact:
    return MembershipFact(tenant, user_id, role, _T0 + timedelta(minutes=minute), by)


def test_membership_facts_carry_the_identity_contract_columns() -> None:
    assert [(c.name, c.nullable) for c in s.membership_facts.columns] == [
        ("id", False),
        ("tenant_id", False),
        ("user_id", False),
        ("role", True),
        ("set_at", False),
        ("set_by", False),
    ]
    assert s.membership_facts.c.id.autoincrement is True


def test_the_newest_fact_is_in_force(engine: sa.Engine, tenants: tuple[TenantId, TenantId]) -> None:
    first, _ = tenants
    repo = MembershipRepository(hub_level_connections(engine))
    repo.record(_fact(first, "usr_b", Role.GUEST, minute=1))
    repo.record(_fact(first, "usr_b", Role.ADMIN, minute=2))

    assert repo.role_in(first, "usr_b") is Role.ADMIN
    assert repo.memberships_for("usr_b") == [Membership(first, "usr_b", Role.ADMIN)]
    assert repo.members(first) == [Membership(first, "usr_b", Role.ADMIN)]


def test_a_fact_with_no_role_revokes(engine: sa.Engine, tenants: tuple[TenantId, TenantId]) -> None:
    first, _ = tenants
    repo = MembershipRepository(hub_level_connections(engine))
    repo.record(_fact(first, "usr_b", Role.ADMIN, minute=1))
    repo.record(_fact(first, "usr_b", None, minute=2))

    assert repo.role_in(first, "usr_b") is None
    assert repo.memberships_for("usr_b") == []
    assert repo.members(first) == []
    assert repo.admin_count(first) == 0

    repo.record(_fact(first, "usr_b", Role.CONTRIBUTOR, minute=3))

    assert repo.role_in(first, "usr_b") is Role.CONTRIBUTOR


def test_a_person_holds_one_membership_per_tenant(engine: sa.Engine, tenants: tuple[TenantId, TenantId]) -> None:
    first, other = tenants
    repo = MembershipRepository(hub_level_connections(engine))
    repo.record(_fact(other, "usr_b", Role.GUEST, minute=1))
    repo.record(_fact(first, "usr_b", Role.ADMIN, minute=2))
    repo.record(_fact(first, "usr_c", Role.CONTRIBUTOR, minute=3))

    assert repo.memberships_for("usr_b") == sorted(
        [Membership(first, "usr_b", Role.ADMIN), Membership(other, "usr_b", Role.GUEST)], key=lambda m: m.tenant_id
    )
    assert repo.members(first) == [Membership(first, "usr_b", Role.ADMIN), Membership(first, "usr_c", Role.CONTRIBUTOR)]
    assert repo.members(other) == [Membership(other, "usr_b", Role.GUEST)]
    assert repo.role_in(other, "usr_c") is None
    assert repo.memberships_for("usr_nobody") == []


def test_admin_count_counts_the_admins_in_force_in_one_tenant(
    engine: sa.Engine, tenants: tuple[TenantId, TenantId]
) -> None:
    first, other = tenants
    repo = MembershipRepository(hub_level_connections(engine))
    repo.record(_fact(first, "usr_b", Role.ADMIN, minute=1))
    repo.record(_fact(first, "usr_c", Role.ADMIN, minute=2))
    repo.record(_fact(first, "usr_c", Role.GUEST, minute=3))
    repo.record(_fact(first, "usr_d", Role.ADMIN, minute=4))
    repo.record(_fact(other, "usr_e", Role.ADMIN, minute=5))

    assert repo.admin_count(first) == 2
    assert repo.admin_count(other) == 1


@pytest.mark.parametrize("by", ["usr_a", SET_BY_MIGRATION, SET_BY_BOOTSTRAP, SET_BY_OPERATOR])
def test_a_fact_keeps_the_time_and_grantor_it_was_given(
    engine: sa.Engine, tenants: tuple[TenantId, TenantId], by: str
) -> None:
    first, _ = tenants
    MembershipRepository(hub_level_connections(engine)).record(_fact(first, "usr_b", Role.GUEST, minute=7, by=by))

    with engine.connect() as conn:
        row = conn.execute(sa.select(s.membership_facts)).one()
    assert (row.tenant_id, row.user_id, row.role, row.set_at, row.set_by) == (
        first,
        "usr_b",
        "guest",
        _T0 + timedelta(minutes=7),
        by,
    )


@pytest.mark.parametrize("role", [Role.SUPERUSER, Role.PENDING])
def test_a_membership_role_is_never_superuser_or_pending(role: Role) -> None:
    with pytest.raises(MembershipRoleRefused, match=role.value):
        _fact(TenantId("ten_1"), "usr_b", role, minute=0)
