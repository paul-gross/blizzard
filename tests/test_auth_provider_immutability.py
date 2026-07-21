"""The boot-time provider-name-immutability check (component tier, issue #92).

``build_hosted_app`` fails with an actionable error when a stored identity names a
provider absent from ``[[auth.oauth.provider]]`` — a rename must not silently orphan
identities and re-mint duplicate users on the next login (issue #92's own AC). Runs
regardless of ``auth.mode`` (an operator flipping back to ``none`` does not erase the
guarantee), but only once the store is confirmed at the expected schema head
(``test_readiness.py``'s own ``build_hosted_app``-must-not-crash-on-drift invariant).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import AuthConfig, ConfigError, OAuthProviderConfig
from blizzard.hub.store import schema as s

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_orphan_identity(config, *, provider_name: str) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(s.users).values(id="usr_1", username="a", display_name="A", email=None, role="guest", created_at=_T0)
        )
        conn.execute(
            insert(s.identities).values(
                provider_name=provider_name, subject="1", user_id="usr_1", handle="a", created_at=_T0
            )
        )


def test_boot_fails_when_a_stored_identity_names_an_unconfigured_provider(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_orphan_identity(config, provider_name="old-github")

    with pytest.raises(ConfigError, match="old-github"):
        hub_app.build_hosted_app(config)


def test_boot_succeeds_when_the_stored_provider_is_still_configured(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    _seed_orphan_identity(config, provider_name="github")
    config = replace(
        config,
        auth=AuthConfig(
            oauth_providers=(
                OAuthProviderConfig(
                    name="github",
                    type="github",
                    display_name="GitHub",
                    client_id="cid",
                    client_secret_env="BZ_OAUTH_GITHUB_SECRET",
                ),
            )
        ),
    )
    config.config_path.write_text(config.to_toml())
    config = hub_app.HubConfig.load(config.root)

    app = hub_app.build_hosted_app(config)
    assert app is not None


def test_boot_succeeds_with_no_stored_identities_at_all(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    app = hub_app.build_hosted_app(config)
    assert app is not None
