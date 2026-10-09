"""Accepting an invitation: a sign-in started from the link carries only the token's hash through the provider round
trip, and on the callback one identity unit of work admits the person — the user and identity link when new, the
membership the invitation grants unless they already hold one there, the ``accepted`` fact, and the session — then
lands them in the invited tenant. A provider that does not vouch for every address it reports, an account carrying no
invited address, and an invitation no longer live or naming nothing admit nobody, write nothing and land on the login
page naming why — the invitation stays live when only the address was wrong; a failure part way leaves nothing behind.
An invitation accepted or revoked between a reader's check and its write goes to whichever outcome was recorded first:
the acceptance that loses is refused as no longer usable, the revocation that loses answers 409. A person already
signed in who opens an invitation goes through the same check."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.auth.invitations import InvitationRecord, InvitationToken, TenantInvitation
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.auth.service import SignInRefusal
from blizzard.hub.store import schema
from blizzard.hub.store.internal.invitation_store import InvitationStore
from blizzard.hub.tenancy import TenantClosed, TenantId
from tests.support import (
    HubHarness,
    build_hub,
    close_tenant,
    default_tenant,
    hub_scoped,
    seed_invitation,
    seed_session,
    seed_user,
)
from tests.test_auth_login_api import FakeOAuthProvider, _state_from_redirect

pytestmark = pytest.mark.component

_WORK = "Ada.Work@Example.com"


def _ada(*, emails: tuple[str, ...] = ("ada@home.example", "ADA.WORK@example.com"), authoritative: bool = True):
    return ProviderIdentity(
        subject="4242",
        handle="ada",
        email=emails[0],
        email_verified=True,
        verified_emails=emails,
        emails_authoritative=authoritative,
    )


def _hub(tmp_path: Path, identity: ProviderIdentity) -> tuple[HubHarness, FakeOAuthProvider]:
    github = FakeOAuthProvider(name="github", type="github")
    github.codes["code"] = identity
    return build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}), github


def _sign_in(hub: HubHarness, token: str, *, session: str | None = None) -> Any:
    cookies = {"bz_session": session} if session is not None else None
    authorize = hub.client.get(f"/api/auth/github/authorize?invite={token}", follow_redirects=False, cookies=cookies)
    state = _state_from_redirect(authorize.headers["location"])
    return hub.client.get(f"/api/auth/github/callback?code=code&state={state}", follow_redirects=False, cookies=cookies)


def _refused(landed: Any, refusal: SignInRefusal) -> None:
    """``landed`` is a refused sign-in: the login page naming ``refusal``, and no session handed out."""
    assert landed.status_code == 302, landed.text
    location = urlsplit(landed.headers["location"])
    assert (location.path, parse_qs(location.query)) == ("/login", {"refusal": [refusal.value]})
    assert "bz_session" not in landed.cookies


def _rows(hub: HubHarness, table: sa.Table) -> list[sa.Row[Any]]:
    with hub.engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(table))).all())


def _written(hub: HubHarness) -> dict[str, int]:
    tables = (schema.users, schema.identities, schema.sessions, schema.membership_facts, schema.invitation_facts)
    return {table.name: len(_rows(hub, table)) for table in tables}


def test_the_sign_in_carries_only_the_tokens_hash_through_the_round_trip(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada())

    hub.client.get("/api/auth/github/authorize?invite=plain-token", follow_redirects=False)

    [entry] = _rows(hub, schema.auth_state)
    assert entry.invitation_token_hash == InvitationToken("plain-token").hash
    assert "plain-token" not in {str(value) for value in entry}


def test_a_new_person_lands_in_the_invited_tenant_with_the_invited_role(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada())
    other = hub.add_tenant("other")
    invitation_id, token = seed_invitation(hub.engine, other.tenant, _WORK, Role.CONTRIBUTOR)

    landed = _sign_in(hub, token)

    assert landed.status_code == 302, landed.text
    location = urlsplit(landed.headers["location"])
    assert location.path == "/invite/accepted"
    assert parse_qs(location.query) == {"tenant": [other.tenant]}
    [user] = _rows(hub, schema.users)
    assert (user.username, user.email) == ("ada", "ada@home.example")
    [link] = _rows(hub, schema.identities)
    assert (link.provider_name, link.subject, link.user_id) == ("github", "4242", user.id)
    [membership] = _rows(hub, schema.membership_facts)
    assert (membership.tenant_id, membership.user_id, membership.role, membership.set_by) == (
        other.tenant,
        user.id,
        "contributor",
        invitation_id,
    )
    [fact] = _rows(hub, schema.invitation_facts)
    assert (fact.tenant_id, fact.invitation_id, fact.kind, fact.actor_user_id) == (
        other.tenant,
        invitation_id,
        "accepted",
        user.id,
    )
    me = hub.client.get("/api/me", cookies={"bz_session": landed.cookies["bz_session"]})
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "ada"


def test_an_existing_member_records_the_acceptance_and_keeps_their_role(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada(emails=("ada@example.com",)))
    member = seed_user(hub, username="ada", role=Role.ADMIN, email="ada@example.com")
    tenant = default_tenant(hub.engine)
    invitation_id, token = seed_invitation(hub.engine, tenant, "ada@example.com", Role.GUEST)
    memberships_before = _rows(hub, schema.membership_facts)

    landed = _sign_in(hub, token)

    assert landed.status_code == 302, landed.text
    assert parse_qs(urlsplit(landed.headers["location"]).query) == {"tenant": [tenant]}
    assert _rows(hub, schema.membership_facts) == memberships_before
    assert hub.hub_services.memberships.role_in(tenant, member.user_id) is Role.ADMIN
    [fact] = _rows(hub, schema.invitation_facts)
    assert (fact.invitation_id, fact.kind, fact.actor_user_id) == (invitation_id, "accepted", member.user_id)


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param(_ada(authoritative=False), id="provider-not-authoritative"),
        pytest.param(_ada(emails=("ada@home.example",)), id="no-invited-address"),
    ],
)
def test_an_identity_the_invitation_does_not_admit_writes_nothing_and_leaves_it_live(
    tmp_path: Path, identity: ProviderIdentity
) -> None:
    hub, github = _hub(tmp_path, identity)
    tenant = default_tenant(hub.engine)
    _invitation_id, token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)
    before = _written(hub)

    _refused(_sign_in(hub, token), SignInRefusal.INVITATION_EMAIL_MISMATCH)

    assert _written(hub) == before
    github.codes["code"] = _ada()
    landed = _sign_in(hub, token)
    assert urlsplit(landed.headers["location"]).path == "/invite/accepted"


def _expired(hub: HubHarness, tenant: TenantId) -> str:
    _invitation_id, token = seed_invitation(
        hub.engine, tenant, _WORK, Role.GUEST, at=hub.clock.now() - timedelta(days=8), lifetime=timedelta(days=7)
    )
    return token


def _revoked(hub: HubHarness, tenant: TenantId) -> str:
    invitation_id, token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)
    with hub.engine.begin() as conn:
        conn.execute(
            hub_scoped(
                sa.insert(schema.invitation_facts).values(
                    tenant_id=tenant,
                    invitation_id=invitation_id,
                    kind="revoked",
                    actor_user_id="usr_admin",
                    recorded_at=hub.clock.now(),
                )
            )
        )
    return token


def _used(hub: HubHarness, tenant: TenantId) -> str:
    _invitation_id, token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)
    assert urlsplit(_sign_in(hub, token).headers["location"]).path == "/invite/accepted"
    return token


def _closed(hub: HubHarness, tenant: TenantId) -> str:
    other = hub.add_tenant("other")
    _invitation_id, token = seed_invitation(hub.engine, other.tenant, _WORK, Role.GUEST)
    close_tenant(hub.engine, other.tenant)
    return token


@pytest.mark.parametrize(
    "invitation",
    [
        pytest.param(_expired, id="expired"),
        pytest.param(_revoked, id="revoked"),
        pytest.param(_used, id="already-used"),
        pytest.param(_closed, id="tenant-closed"),
        pytest.param(lambda _hub, _tenant: "no-such-token", id="names-nothing"),
    ],
)
def test_an_invitation_no_longer_usable_writes_nothing(tmp_path: Path, invitation: Any) -> None:
    hub, _github = _hub(tmp_path, _ada())
    token = invitation(hub, default_tenant(hub.engine))
    before = _written(hub)

    _refused(_sign_in(hub, token), SignInRefusal.INVITATION_UNUSABLE)

    assert _written(hub) == before


def test_a_signed_in_person_is_admitted_into_one_more_tenant_when_their_account_carries_the_address(
    tmp_path: Path,
) -> None:
    hub, _github = _hub(tmp_path, _ada())
    member = seed_user(hub, username="ada", role=Role.CONTRIBUTOR, email="ada@home.example")
    session, _ = hub.hub_services.auth.mint_session(member)
    other = hub.add_tenant("other")
    _invitation_id, token = seed_invitation(hub.engine, other.tenant, _WORK, Role.GUEST)

    landed = _sign_in(hub, token, session=session)

    assert parse_qs(urlsplit(landed.headers["location"]).query) == {"tenant": [other.tenant]}
    assert hub.hub_services.memberships.role_in(other.tenant, member.user_id) is Role.GUEST
    assert hub.hub_services.memberships.role_in(default_tenant(hub.engine), member.user_id) is Role.CONTRIBUTOR


def test_a_signed_in_person_whose_account_lacks_the_address_is_refused_and_keeps_their_session(
    tmp_path: Path,
) -> None:
    hub, _github = _hub(tmp_path, _ada(emails=("ada@home.example",)))
    member = seed_user(hub, username="ada", role=Role.CONTRIBUTOR, email="ada@home.example")
    session, _ = hub.hub_services.auth.mint_session(member)
    other = hub.add_tenant("other")
    _invitation_id, token = seed_invitation(hub.engine, other.tenant, _WORK, Role.GUEST)
    before = _written(hub)

    _refused(_sign_in(hub, token, session=session), SignInRefusal.INVITATION_EMAIL_MISMATCH)

    assert _written(hub) == before
    assert hub.hub_services.memberships.role_in(other.tenant, member.user_id) is None
    me = hub.client.get("/api/me", cookies={"bz_session": session})
    assert me.status_code == 200, me.text


def test_an_invitation_is_accepted_at_most_once(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada())
    tenant = default_tenant(hub.engine)
    invitation_id, _token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)
    invitation = TenantInvitation(tenant, invitation_id)
    assert hub.hub_services.auth.accept_invitation(_ada(), provider_name="github", invitation=invitation) is not None
    after_first = _written(hub)

    again = hub.hub_services.auth.accept_invitation(_ada(), provider_name="github", invitation=invitation)

    assert again is SignInRefusal.INVITATION_UNUSABLE
    assert _written(hub) == after_first


def resolved_after_first_read(monkeypatch: pytest.MonkeyPatch, rival: Callable[[], object]) -> None:
    """Interleave ``rival`` between the next invitation read and its reader's write: that read answers the invitation
    as it stood before any outcome was recorded — live — and ``rival`` runs right after it; every later read is real."""
    real_get = InvitationStore.get
    reads = 0

    def get(store: InvitationStore, invitation_id: str) -> InvitationRecord | None:
        nonlocal reads
        reads += 1
        record = real_get(store, invitation_id)
        if reads > 1 or record is None:
            return record
        rival()
        return InvitationRecord(record.invitation, None)

    monkeypatch.setattr(InvitationStore, "get", get)


def test_a_revocation_that_loses_to_an_acceptance_answers_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _github = _hub(tmp_path, _ada())
    tenant = default_tenant(hub.engine)
    admin = {"Cookie": f"bz_session={seed_session(hub, seed_user(hub, username='admin', role=Role.ADMIN))}"}
    invitation_id, token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)

    def accepted() -> None:
        assert urlsplit(_sign_in(hub, token).headers["location"]).path == "/invite/accepted"

    resolved_after_first_read(monkeypatch, accepted)

    revoked = hub.client.post(f"/api/invitations/{invitation_id}/revocations", headers=admin)

    assert revoked.status_code == 409, revoked.text
    assert revoked.json()["detail"] == f"invitation {invitation_id} was already accepted"
    assert [row.kind for row in _rows(hub, schema.invitation_facts)] == ["accepted"]


def test_an_acceptance_that_loses_to_a_revocation_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, _github = _hub(tmp_path, _ada())
    token = _revoked(hub, default_tenant(hub.engine))
    before = _written(hub)
    read_live: list[bool] = []
    resolved_after_first_read(monkeypatch, lambda: read_live.append(True))

    _refused(_sign_in(hub, token), SignInRefusal.INVITATION_UNUSABLE)

    assert read_live == [True]
    assert _written(hub) == before
    assert [row.kind for row in _rows(hub, schema.invitation_facts)] == ["revoked"]


def test_a_failure_part_way_leaves_nothing_behind(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada())
    other = hub.add_tenant("other")
    invitation_id, _token = seed_invitation(hub.engine, other.tenant, _WORK, Role.GUEST)
    close_tenant(hub.engine, other.tenant)
    before = _written(hub)

    with pytest.raises(TenantClosed):
        hub.hub_services.auth.accept_invitation(
            _ada(), provider_name="github", invitation=TenantInvitation(other.tenant, invitation_id)
        )

    assert _written(hub) == before
