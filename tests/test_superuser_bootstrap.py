"""The ``auth.superuser`` bootstrap lifecycle at boot (component tier).

Exercises the lifecycle against a real migrated store: boot records the configured
target unclaimed and reports it while it stays so, promotes nobody — the user holding the
email claims it at sign-in — leaves a claimed target alone, and never revokes anyone when
the configured email changes or is unset.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.config import AuthConfig
from blizzard.hub.store import schema as s

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_user(config, *, user_id: str, email: str) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(id=user_id, username=user_id, display_name=user_id, email=email, created_at=_T0)
        )


def _with_superuser(config, email: str | None):  # type: ignore[no-untyped-def]
    config = replace(config, auth=AuthConfig(mode="oauth", superuser=email))
    config.config_path.write_text(config.to_toml())
    return hub_app.HubConfig.load(config.root)


def test_boot_pre_provisions_an_unclaimed_row_when_no_user_matches(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    config = _with_superuser(config, "alice@example.com")

    app = hub_app.build_hosted_app(config)

    services = app.state.services
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.email == "alice@example.com"
    assert bootstrap.claimed_user_id is None
    kinds = [f.kind for f in services.auth_facts.list_recent()]
    assert kinds.count("superuser_bootstrap_unclaimed") == 1


def test_boot_reports_unclaimed_again_on_a_second_boot(tmp_path: Path) -> None:
    """Surfaced at *every* boot while unclaimed — never a silent dead end."""
    config = hub_runtime.init_environment(tmp_path / "hub")
    config = _with_superuser(config, "alice@example.com")

    hub_app.build_hosted_app(config)
    app = hub_app.build_hosted_app(config)

    kinds = [f.kind for f in app.state.services.auth_facts.list_recent()]
    assert kinds.count("superuser_bootstrap_unclaimed") == 2


def _hub_admins(config) -> list[str]:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(config.db_url)
    with engine.connect() as conn:
        return list(conn.execute(select(s.hub_admin_facts.c.user_id).where(s.hub_admin_facts.c.granted)).scalars())


def _claimed(config, *, user_id: str, email: str) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(insert(s.superuser_bootstrap).values(email=email, claimed_user_id=user_id, updated_at=_T0))
        conn.execute(insert(s.hub_admin_facts).values(user_id=user_id, granted=True, set_at=_T0, set_by="bootstrap"))


def test_boot_promotes_nobody_and_leaves_an_existing_user_to_claim_at_sign_in(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_1", email="alice@example.com")
    config = _with_superuser(config, "alice@example.com")

    app = hub_app.build_hosted_app(config)

    services = app.state.services
    assert _hub_admins(config) == []
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id is None
    kinds = [f.kind for f in services.auth_facts.list_recent()]
    assert kinds.count("superuser_bootstrap_unclaimed") == 1


def test_boot_leaves_a_claimed_target_alone(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_1", email="alice@example.com")
    _claimed(config, user_id="usr_1", email="alice@example.com")
    config = _with_superuser(config, "alice@example.com")

    app = hub_app.build_hosted_app(config)

    services = app.state.services
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == "usr_1"
    assert services.auth_facts.list_recent() == []


def test_changing_auth_superuser_records_a_new_target_and_revokes_nobody(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_alice", email="alice@example.com")
    _seed_user(config, user_id="usr_bob", email="bob@example.com")
    _claimed(config, user_id="usr_alice", email="alice@example.com")

    config = _with_superuser(config, "bob@example.com")
    app = hub_app.build_hosted_app(config)

    bootstrap = app.state.services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert (bootstrap.email, bootstrap.claimed_user_id) == ("bob@example.com", None)
    assert _hub_admins(config) == ["usr_alice"]


def test_unsetting_auth_superuser_does_nothing(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_alice", email="alice@example.com")
    _claimed(config, user_id="usr_alice", email="alice@example.com")

    app = hub_app.build_hosted_app(_with_superuser(config, None))

    bootstrap = app.state.services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == "usr_alice"
    assert _hub_admins(config) == ["usr_alice"]


def test_boot_with_no_superuser_configured_does_nothing(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")

    app = hub_app.build_hosted_app(config)

    assert app.state.services.auth.get_superuser_bootstrap() is None


def test_the_first_sign_in_claims_the_hub_administrator_and_the_first_tenants_admin(tmp_path: Path) -> None:
    """On a fresh hub, the claim, the hub administrator fact and the admin membership land in one transaction, each
    ``bootstrap``."""
    config = _with_superuser(hub_runtime.init_environment(tmp_path / "hub"), "alice@example.com")
    services = hub_app.build_hosted_app(config).state.services

    user = services.auth.link_or_mint(
        ProviderIdentity(subject="gh-alice", handle="alice", email="alice@example.com", email_verified=True),
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
