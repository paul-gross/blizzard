"""The boot-time provider-name-immutability check (component tier).

``build_hosted_app`` fails loud when a stored identity names a provider absent from
``[[auth.oauth.provider]]`` — a rename must not silently orphan identities. Runs
regardless of ``auth.mode``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import AuthConfig, ConfigError, OAuthProviderConfig
from blizzard.hub.store import schema as s
from tests.config_inputs import build_hosted_hub_app, init_hub, load_hub_config

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_orphan_identity(config, *, provider_name: str) -> None:  #
    engine = create_engine_from_url(config.db_url)
    with engine.begin() as conn:
        conn.execute(insert(s.users).values(id="usr_1", username="a", display_name="A", email=None, created_at=_T0))
        conn.execute(
            insert(s.identities).values(
                provider_name=provider_name, subject="1", user_id="usr_1", handle="a", created_at=_T0
            )
        )


def test_boot_fails_when_a_stored_identity_names_an_unconfigured_provider(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    _seed_orphan_identity(config, provider_name="old-github")

    with pytest.raises(ConfigError, match="old-github"):
        build_hosted_hub_app(config)


def test_boot_succeeds_when_the_stored_provider_is_still_configured(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
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
    config = load_hub_config(config.root)

    app = build_hosted_hub_app(config)
    assert app is not None


def test_boot_succeeds_with_no_stored_identities_at_all(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    app = build_hosted_hub_app(config)
    assert app is not None
