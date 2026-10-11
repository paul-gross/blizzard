"""Memberships as facts over a migrated store: the newest fact per ``(tenant, user)`` is in force, a fact with no role
revokes, a person's memberships span tenants while each tenant's members stay its own, and a membership's role is
only ``guest``, ``contributor`` or ``admin``. Changing one: nobody changes their own role, a non-member is touched only
under ``MEMBERSHIP_GRANT_ANY``, a tenant keeps its last admin, and a change to the role in force writes nothing."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog

from blizzard.auth_core import Permission, Role, permissions_for
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.hub_admins import HubAdminFact
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.memberships import (
    SET_BY_BOOTSTRAP,
    SET_BY_MIGRATION,
    SET_BY_OPERATOR,
    LastAdminKept,
    Member,
    Membership,
    MembershipChangeRefused,
    MembershipFact,
    MembershipRoleRefused,
    NotAMember,
    TenantMembers,
    assignable_roles,
    membership_change_refusal,
)
from blizzard.hub.auth.models import ResolvedIdentity, User
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


# --- changing a membership ----------------------------------------------------


def _user(user_id: str) -> User:
    return User(user_id, user_id.removeprefix("usr_"), user_id, None, _T0)


def _actor(user_id: str, role: Role | None, *, hub_admin: bool = False) -> ResolvedIdentity:
    return ResolvedIdentity(
        user_id=user_id,
        username=user_id.removeprefix("usr_"),
        display_name=user_id,
        tenant=_OTHER,
        role=role,
        permissions=permissions_for(role, hub_admin),
        hub_admin=hub_admin,
    )


def _subject(user_id: str, role: Role | None, *, hub_admin: bool = False) -> Member:
    return Member(_user(user_id), _OTHER, role, hub_admin)


_ADMIN = _actor("usr_a", Role.ADMIN)
_GRANT_ANY = ResolvedIdentity("usr_h", "h", "usr_h", _OTHER, None, frozenset({Permission.MEMBERSHIP_GRANT_ANY}), True)


@pytest.mark.parametrize(
    ("actor", "subject", "to_role", "admins", "refused"),
    [
        pytest.param(_ADMIN, _subject("usr_b", Role.GUEST), Role.ADMIN, 1, None, id="tenant-admin-grants-admin"),
        pytest.param(_ADMIN, _subject("usr_b", Role.ADMIN), Role.GUEST, 2, None, id="tenant-admin-demotes-an-admin"),
        pytest.param(_ADMIN, _subject("usr_b", Role.GUEST, hub_admin=True), None, 1, None, id="hub-admin-subject"),
        pytest.param(_ADMIN, _subject("usr_a", Role.ADMIN), Role.GUEST, 2, MembershipChangeRefused, id="self"),
        pytest.param(_ADMIN, _subject("usr_b", None), Role.GUEST, 1, NotAMember, id="non-member"),
        pytest.param(_GRANT_ANY, _subject("usr_b", None), Role.ADMIN, 0, None, id="grant-any-non-member"),
        pytest.param(_GRANT_ANY, _subject("usr_h", None), Role.ADMIN, 0, MembershipChangeRefused, id="grant-any-self"),
        pytest.param(_GRANT_ANY, _subject("usr_b", Role.ADMIN), Role.GUEST, 1, LastAdminKept, id="demote-last-admin"),
        pytest.param(_GRANT_ANY, _subject("usr_b", Role.ADMIN), None, 1, LastAdminKept, id="revoke-last-admin"),
    ],
)
def test_the_membership_change_rules(
    actor: ResolvedIdentity, subject: Member, to_role: Role | None, admins: int, refused: type[Exception] | None
) -> None:
    refusal = membership_change_refusal(actor=actor, subject=subject, to_role=to_role, admins=admins)
    assert (refusal is None) if refused is None else isinstance(refusal, refused)


def test_a_non_member_is_refused_as_an_unknown_user_would_be() -> None:
    refusal = membership_change_refusal(actor=_ADMIN, subject=_subject("usr_b", None), to_role=Role.GUEST, admins=1)
    assert str(refusal) == "no such user"


def test_the_last_admin_may_only_stay_admin() -> None:
    assert assignable_roles(actor=_GRANT_ANY, subject=_subject("usr_b", Role.ADMIN), admins=1) == [Role.ADMIN]


@pytest.fixture
def members(engine: sa.Engine, tenants: tuple[TenantId, TenantId]) -> TenantMembers:
    del tenants
    errors = RepoErrorFactory(structlog.get_logger("test"))
    connections = hub_level_connections(engine)
    users = UserRepository(connections, errors)
    for user_id in ("usr_a", "usr_b", "usr_c", "usr_h"):
        users.create(_user(user_id))
    repo = MembershipRepository(connections)
    repo.record(_fact(_OTHER, "usr_a", Role.ADMIN, minute=0))
    repo.record(_fact(_OTHER, "usr_b", Role.GUEST, minute=0))
    HubAdminRepository(connections).record(HubAdminFact("usr_h", True, _T0, SET_BY_BOOTSTRAP))
    clock = FixedClock(_T0 + timedelta(hours=1))
    return TenantMembers(
        memberships=repo,
        hub_admins=HubAdminRepository(connections),
        users=users,
        transactions=IdentityTransactions(connections, errors),
        auth_facts=AuthFactsService(facts=AuthFactsRepository(connections), clock=clock),
        clock=clock,
    )


def _facts(engine: sa.Engine) -> list[tuple[str, str | None, str]]:
    with engine.connect() as conn:
        rows = conn.execute(sa.select(s.membership_facts).order_by(s.membership_facts.c.id)).all()
    return [(r.user_id, r.role, r.set_by) for r in rows]


def test_a_change_records_the_membership_and_the_security_fact(engine: sa.Engine, members: TenantMembers) -> None:
    changed = members.change(actor=_ADMIN, subject=members.member(_user("usr_b"), tenant=_OTHER), to_role=Role.ADMIN)

    assert changed.role is Role.ADMIN
    assert _facts(engine)[-1] == ("usr_b", "admin", "usr_a")
    fact = AuthFactsRepository(hub_level_connections(engine)).list_recent()[0]
    assert (fact.kind, fact.actor, fact.subject, fact.detail) == (
        "user_role_changed",
        "a",
        "b",
        f"guest -> admin in {_OTHER}",
    )


def test_a_revocation_records_no_role(engine: sa.Engine, members: TenantMembers) -> None:
    members.change(actor=_ADMIN, subject=members.member(_user("usr_b"), tenant=_OTHER), to_role=None)

    assert _facts(engine)[-1] == ("usr_b", None, "usr_a")
    assert members.member(_user("usr_b"), tenant=_OTHER).role is None


def test_a_change_to_the_role_in_force_writes_nothing(engine: sa.Engine, members: TenantMembers) -> None:
    before = _facts(engine)

    unchanged = members.change(actor=_ADMIN, subject=members.member(_user("usr_b"), tenant=_OTHER), to_role=Role.GUEST)

    assert unchanged.role is Role.GUEST
    assert _facts(engine) == before
    assert AuthFactsRepository(hub_level_connections(engine)).list_recent() == []


def test_membership_grant_any_brings_in_a_non_member(engine: sa.Engine, members: TenantMembers) -> None:
    members.change(actor=_GRANT_ANY, subject=members.member(_user("usr_c"), tenant=_OTHER), to_role=Role.CONTRIBUTOR)

    assert _facts(engine)[-1] == ("usr_c", "contributor", "usr_h")


def test_a_refused_change_writes_nothing(engine: sa.Engine, members: TenantMembers) -> None:
    before = _facts(engine)

    with pytest.raises(NotAMember):
        members.change(actor=_ADMIN, subject=members.member(_user("usr_c"), tenant=_OTHER), to_role=Role.GUEST)
    with pytest.raises(LastAdminKept):
        members.change(actor=_GRANT_ANY, subject=members.member(_user("usr_a"), tenant=_OTHER), to_role=None)

    assert _facts(engine) == before


def test_the_listing_holds_the_tenants_members_alone(
    engine: sa.Engine, tenants: tuple[TenantId, TenantId], members: TenantMembers
) -> None:
    MembershipRepository(hub_level_connections(engine)).record(_fact(tenants[0], "usr_c", Role.GUEST, minute=1))

    listed = members.listing(_OTHER)

    assert [(m.user.user_id, m.role, m.hub_admin) for m in listed] == [
        ("usr_a", Role.ADMIN, False),
        ("usr_b", Role.GUEST, False),
    ]
