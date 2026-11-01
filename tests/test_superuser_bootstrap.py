"""The ``auth.superuser`` bootstrap lifecycle at boot (component tier).

Exercises the lifecycle against a real migrated store: boot records the configured
target unclaimed and reports it while it stays so, promotes nobody — the user holding the
email claims it at sign-in — leaves a claimed target alone, cancels a target nobody has
claimed once the email is unset, and never revokes anyone when the configured email changes
or is unset.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.auth.models import ProviderIdentity, User
from blizzard.hub.config import AuthConfig
from blizzard.hub.store import schema as s
from tests.config_inputs import build_hosted_hub_app, init_hub, load_hub_config

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_user(config, *, user_id: str, email: str) -> None:
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(id=user_id, username=user_id, display_name=user_id, email=email, created_at=_T0)
        )


def _with_superuser(config, email: str | None):
    config = replace(config, auth=AuthConfig(mode="oauth", superuser=email))
    config.config_path.write_text(config.to_toml())
    return load_hub_config(config.root)


def test_boot_pre_provisions_an_unclaimed_row_when_no_user_matches(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    config = _with_superuser(config, "alice@example.com")

    app = build_hosted_hub_app(config)

    services = app.state.services
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.email == "alice@example.com"
    assert bootstrap.claimed_user_id is None
    kinds = [f.kind for f in services.auth_facts.list_recent()]
    assert kinds.count("superuser_bootstrap_unclaimed") == 1


def test_boot_reports_unclaimed_again_on_a_second_boot(tmp_path: Path) -> None:
    """Surfaced at *every* boot while unclaimed — never a silent dead end."""
    config = init_hub(tmp_path / "hub")
    config = _with_superuser(config, "alice@example.com")

    build_hosted_hub_app(config)
    app = build_hosted_hub_app(config)

    kinds = [f.kind for f in app.state.services.auth_facts.list_recent()]
    assert kinds.count("superuser_bootstrap_unclaimed") == 2


def _hub_admins(config) -> list[str]:
    engine = create_engine_from_url(config.db_url)
    with engine.connect() as conn:
        return list(conn.execute(select(s.hub_admin_facts.c.user_id).where(s.hub_admin_facts.c.granted)).scalars())


def _claimed(config, *, user_id: str, email: str) -> None:
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(insert(s.superuser_bootstrap).values(email=email, claimed_user_id=user_id, updated_at=_T0))
        conn.execute(insert(s.hub_admin_facts).values(user_id=user_id, granted=True, set_at=_T0, set_by="bootstrap"))


def test_boot_promotes_nobody_and_leaves_an_existing_user_to_claim_at_sign_in(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    _seed_user(config, user_id="usr_1", email="alice@example.com")
    config = _with_superuser(config, "alice@example.com")

    app = build_hosted_hub_app(config)

    services = app.state.services
    assert _hub_admins(config) == []
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id is None
    kinds = [f.kind for f in services.auth_facts.list_recent()]
    assert kinds.count("superuser_bootstrap_unclaimed") == 1


def test_boot_leaves_a_claimed_target_alone(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    _seed_user(config, user_id="usr_1", email="alice@example.com")
    _claimed(config, user_id="usr_1", email="alice@example.com")
    config = _with_superuser(config, "alice@example.com")

    app = build_hosted_hub_app(config)

    services = app.state.services
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == "usr_1"
    assert services.auth_facts.list_recent() == []


def test_changing_auth_superuser_records_a_new_target_and_revokes_nobody(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    _seed_user(config, user_id="usr_alice", email="alice@example.com")
    _seed_user(config, user_id="usr_bob", email="bob@example.com")
    _claimed(config, user_id="usr_alice", email="alice@example.com")

    config = _with_superuser(config, "bob@example.com")
    app = build_hosted_hub_app(config)

    bootstrap = app.state.services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert (bootstrap.email, bootstrap.claimed_user_id) == ("bob@example.com", None)
    assert _hub_admins(config) == ["usr_alice"]


def _sign_in(services, *, handle: str) -> User:
    return services.auth.link_or_mint(
        ProviderIdentity(
            subject=f"gh-{handle}",
            handle=handle,
            email=f"{handle}@example.com",
            email_verified=True,
            emails_authoritative=True,
        ),
        provider_name="github",
    )


def _admin_memberships(config) -> list[str]:
    engine = create_engine_from_url(config.db_url)
    with engine.connect() as conn:
        rows = conn.execute(select(s.membership_facts.c.user_id).where(s.membership_facts.c.role == "admin"))
        return list(rows.scalars())


def test_unsetting_auth_superuser_before_any_claim_cancels_the_intent(tmp_path: Path) -> None:
    config = _with_superuser(init_hub(tmp_path / "hub"), "alice@example.com")
    build_hosted_hub_app(config)

    services = build_hosted_hub_app(_with_superuser(config, None)).state.services
    _sign_in(services, handle="alice")

    assert services.auth.get_superuser_bootstrap() is None
    assert _hub_admins(config) == []
    assert _admin_memberships(config) == []


def test_unsetting_auth_superuser_after_the_claim_keeps_the_administrator(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    _seed_user(config, user_id="usr_alice", email="alice@example.com")
    _claimed(config, user_id="usr_alice", email="alice@example.com")

    app = build_hosted_hub_app(_with_superuser(config, None))

    bootstrap = app.state.services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert (bootstrap.email, bootstrap.claimed_user_id) == ("alice@example.com", "usr_alice")
    assert _hub_admins(config) == ["usr_alice"]


def test_unsetting_a_changed_target_before_it_signs_in_grants_it_nothing(tmp_path: Path) -> None:
    """The first claimant stays a hub administrator; the newer target, unset before signing in, gains nothing."""
    config = init_hub(tmp_path / "hub")
    _seed_user(config, user_id="usr_alice", email="alice@example.com")
    _claimed(config, user_id="usr_alice", email="alice@example.com")
    build_hosted_hub_app(_with_superuser(config, "bob@example.com"))

    services = build_hosted_hub_app(_with_superuser(config, None)).state.services
    _sign_in(services, handle="bob")

    assert services.auth.get_superuser_bootstrap() is None
    assert _hub_admins(config) == ["usr_alice"]
    assert _admin_memberships(config) == []


def _before_the_claims_unit(monkeypatch: pytest.MonkeyPatch, step) -> None:
    """Run ``step`` once, after the claim's unlocked check and before its unit of work takes the identity lock."""
    begin = IdentityTransactions.begin

    def begin_after_step(self: IdentityTransactions):
        monkeypatch.setattr(IdentityTransactions, "begin", begin)
        step()
        return begin(self)

    monkeypatch.setattr(IdentityTransactions, "begin", begin_after_step)


def test_a_claim_finds_nothing_once_the_intent_is_cancelled_ahead_of_its_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _with_superuser(init_hub(tmp_path / "hub"), "alice@example.com")
    services = build_hosted_hub_app(config).state.services
    _before_the_claims_unit(monkeypatch, services.auth.cancel_unclaimed_superuser_bootstrap)

    _sign_in(services, handle="alice")

    assert services.auth.get_superuser_bootstrap() is None
    assert _hub_admins(config) == []
    assert _admin_memberships(config) == []


def test_a_claim_never_takes_an_intent_superseded_ahead_of_its_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _with_superuser(init_hub(tmp_path / "hub"), "alice@example.com")
    services = build_hosted_hub_app(config).state.services
    _before_the_claims_unit(
        monkeypatch, lambda: services.auth.record_superuser_bootstrap(email="bob@example.com", claimed_user_id=None)
    )

    _sign_in(services, handle="alice")

    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert (bootstrap.email, bootstrap.claimed_user_id) == ("bob@example.com", None)
    assert _hub_admins(config) == []
    assert _admin_memberships(config) == []


def test_boot_with_no_superuser_configured_does_nothing(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")

    app = build_hosted_hub_app(config)

    assert app.state.services.auth.get_superuser_bootstrap() is None


def test_the_first_sign_in_claims_the_hub_administrator_and_the_first_tenants_admin(tmp_path: Path) -> None:
    """On a fresh hub, the claim, the hub administrator fact and the admin membership land in one transaction, each
    ``bootstrap``."""
    config = _with_superuser(init_hub(tmp_path / "hub"), "alice@example.com")
    services = build_hosted_hub_app(config).state.services

    user = services.auth.link_or_mint(
        ProviderIdentity(
            subject="gh-alice",
            handle="alice",
            email="alice@example.com",
            email_verified=True,
            emails_authoritative=True,
        ),
        provider_name="github",
    )

    engine = create_engine_from_url(config.db_url)
    with engine.connect() as conn:
        tenant = conn.execute(select(s.tenants.c.tenant_id)).scalar_one()
        hub_admins = conn.execute(select(s.hub_admin_facts.c.user_id, s.hub_admin_facts.c.set_by)).all()
        memberships = conn.execute(
            select(s.membership_facts.c.tenant_id, s.membership_facts.c.user_id, s.membership_facts.c.role)
        ).all()
    assert [tuple(row) for row in hub_admins] == [(user.user_id, "bootstrap")]
    assert [tuple(row) for row in memberships] == [(tenant, user.user_id, "admin")]
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == user.user_id
