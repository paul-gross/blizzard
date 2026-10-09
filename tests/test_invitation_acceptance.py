"""Accepting an invitation: a sign-in started from the link carries only the token's hash through the provider round
trip, and on the callback one identity unit of work admits the person — the user and identity link when new, the
membership the invitation grants unless they already hold one there, the ``accepted`` fact, and the session — then
lands them in the invited tenant. A provider that does not vouch for every address it reports, an account carrying no
invited address, and an invitation no longer live admit nobody and write nothing; a failure part way leaves nothing
behind."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa

from blizzard.auth_core import Role
from blizzard.hub.auth.invitations import InvitationToken, TenantInvitation
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantClosed, TenantId
from tests.support import HubHarness, build_hub, default_tenant, hub_scoped, seed_invitation, seed_user
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


def _sign_in(hub: HubHarness, token: str) -> Any:
    authorize = hub.client.get(f"/api/auth/github/authorize?invite={token}", follow_redirects=False)
    state = _state_from_redirect(authorize.headers["location"])
    return hub.client.get(f"/api/auth/github/callback?code=code&state={state}", follow_redirects=False)


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
def test_an_identity_the_invitation_does_not_admit_writes_nothing(tmp_path: Path, identity: ProviderIdentity) -> None:
    hub, _github = _hub(tmp_path, identity)
    tenant = default_tenant(hub.engine)
    invitation_id, _token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)
    before = _written(hub)

    accepted = hub.hub_services.auth.accept_invitation(
        identity, provider_name="github", invitation=TenantInvitation(tenant, invitation_id)
    )

    assert accepted is None
    assert _written(hub) == before


def test_an_invitation_is_accepted_at_most_once(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada())
    tenant = default_tenant(hub.engine)
    invitation_id, _token = seed_invitation(hub.engine, tenant, _WORK, Role.GUEST)
    invitation = TenantInvitation(tenant, invitation_id)
    assert hub.hub_services.auth.accept_invitation(_ada(), provider_name="github", invitation=invitation) is not None
    after_first = _written(hub)

    again = hub.hub_services.auth.accept_invitation(_ada(), provider_name="github", invitation=invitation)

    assert again is None
    assert _written(hub) == after_first


def test_a_failure_part_way_leaves_nothing_behind(tmp_path: Path) -> None:
    hub, _github = _hub(tmp_path, _ada())
    other = hub.add_tenant("other")
    invitation_id, _token = seed_invitation(hub.engine, other.tenant, _WORK, Role.GUEST)
    _close(hub, other.tenant)
    before = _written(hub)

    with pytest.raises(TenantClosed):
        hub.hub_services.auth.accept_invitation(
            _ada(), provider_name="github", invitation=TenantInvitation(other.tenant, invitation_id)
        )

    assert _written(hub) == before


def _close(hub: HubHarness, tenant: TenantId) -> None:
    """Close ``tenant`` as a deletion's first step records it — its writes are refused from then on."""
    with hub.engine.begin() as conn:
        conn.execute(
            hub_scoped(
                sa.insert(schema.tenant_deletions).values(
                    tenant_id=tenant, name="other", deleted_by="test", deleted_at=None
                )
            )
        )
