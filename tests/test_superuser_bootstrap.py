"""The ``auth.superuser`` bootstrap lifecycle at boot (component tier, issue #94).

``build_hosted_app`` runs ``ensure_superuser_bootstrap`` once the store is confirmed at
the expected schema head, mirroring the provider-name-immutability check's own
``readiness.evaluate().ready`` guard (``tests/test_auth_provider_immutability.py``).
Exercises the full lifecycle against a real migrated store: pre-provision when unclaimed,
promote-in-place when the email already resolves to a user, idempotence across repeated
boots, and the config-change demotion of the *previous* bootstrapped superuser.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import AuthConfig
from blizzard.hub.store import schema as s

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_user(config, *, user_id: str, email: str, role: Role = Role.GUEST) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(
                id=user_id, username=user_id, display_name=user_id, email=email, role=role.value, created_at=_T0
            )
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


def test_boot_promotes_an_existing_verified_user_directly(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_1", email="alice@example.com")
    config = _with_superuser(config, "alice@example.com")

    app = hub_app.build_hosted_app(config)

    services = app.state.services
    user = services.users.get("usr_1")
    assert user is not None
    assert user.role is Role.SUPERUSER
    bootstrap = services.auth.get_superuser_bootstrap()
    assert bootstrap is not None
    assert bootstrap.claimed_user_id == "usr_1"


def test_boot_is_idempotent_when_the_user_already_holds_superuser(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_1", email="alice@example.com")
    config = _with_superuser(config, "alice@example.com")

    hub_app.build_hosted_app(config)
    app = hub_app.build_hosted_app(config)

    services = app.state.services
    user = services.users.get("usr_1")
    assert user is not None
    assert user.role is Role.SUPERUSER
    role_changes = [f for f in services.auth_facts.list_recent() if f.kind == "user_role_changed"]
    assert len(role_changes) == 1  # promoted once, not re-promoted on the second boot


def test_changing_auth_superuser_demotes_the_previous_bootstrapped_superuser(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_user(config, user_id="usr_alice", email="alice@example.com")
    _seed_user(config, user_id="usr_bob", email="bob@example.com")
    config = _with_superuser(config, "alice@example.com")
    hub_app.build_hosted_app(config)

    config = _with_superuser(config, "bob@example.com")
    app = hub_app.build_hosted_app(config)

    services = app.state.services
    alice = services.users.get("usr_alice")
    bob = services.users.get("usr_bob")
    assert alice is not None
    assert bob is not None
    assert alice.role is Role.ADMIN  # demoted
    assert bob.role is Role.SUPERUSER
    demotion_facts = [
        f for f in services.auth_facts.list_recent() if f.kind == "user_role_changed" and f.subject == "usr_alice"
    ]
    assert any(f.detail == "superuser -> admin" for f in demotion_facts)


def test_boot_with_no_superuser_configured_does_nothing(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")

    app = hub_app.build_hosted_app(config)

    assert app.state.services.auth.get_superuser_bootstrap() is None
