"""``[auth]`` config parsing (issue #91) — ``mode``/``superuser`` validated,
``[[auth.oauth.provider]]`` structurally parsed-and-carried (semantic validation is #92's).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH, ConfigError, HubConfig

pytestmark = pytest.mark.unit


def _write(root: Path, body: str) -> None:
    root.mkdir(exist_ok=True)
    (root / "blizzard-hub.toml").write_text(f'db_url = "sqlite:///{root}/hub.db"\n{body}')


def test_scaffold_defaults_to_none_mode(tmp_path: Path) -> None:
    config = HubConfig.scaffold(tmp_path)
    assert config.auth.mode == AUTH_MODE_NONE
    assert config.auth.superuser is None
    assert config.auth.oauth_providers == ()


def test_missing_auth_block_defaults_to_none_mode(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, "")
    config = HubConfig.load(root)
    assert config.auth.mode == AUTH_MODE_NONE


def test_oauth_mode_and_superuser_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, '[auth]\nmode = "oauth"\nsuperuser = "ada@example.com"\n')
    config = HubConfig.load(root)
    assert config.auth.mode == AUTH_MODE_OAUTH
    assert config.auth.superuser == "ada@example.com"


def test_unknown_auth_mode_rejected(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, '[auth]\nmode = "bogus"\n')
    with pytest.raises(ConfigError, match="auth\\.mode"):
        HubConfig.load(root)


def test_oauth_provider_entry_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(
        root,
        "[[auth.oauth.provider]]\n"
        'name = "github"\n'
        'type = "github"\n'
        'display_name = "GitHub"\n'
        'client_id = "abc123"\n'
        'client_secret_env = "BZ_OAUTH_GITHUB_SECRET"\n',
    )
    config = HubConfig.load(root)
    assert len(config.auth.oauth_providers) == 1
    provider = config.auth.oauth_providers[0]
    assert provider.name == "github"
    assert provider.type == "github"
    assert provider.display_name == "GitHub"
    assert provider.client_id == "abc123"
    assert provider.client_secret_env == "BZ_OAUTH_GITHUB_SECRET"
    assert provider.issuer is None


def test_oauth_provider_missing_required_key_rejected(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, '[[auth.oauth.provider]]\nname = "github"\n')
    with pytest.raises(ConfigError, match="missing required key"):
        HubConfig.load(root)


def test_duplicate_oauth_provider_name_rejected(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    entry = (
        "[[auth.oauth.provider]]\n"
        'name = "github"\n'
        'type = "github"\n'
        'display_name = "GitHub"\n'
        'client_id = "abc123"\n'
        'client_secret_env = "BZ_OAUTH_GITHUB_SECRET"\n'
    )
    _write(root, entry + entry)
    with pytest.raises(ConfigError, match="duplicate"):
        HubConfig.load(root)


def test_to_toml_round_trips_through_load(tmp_path: Path) -> None:
    """``to_toml`` → ``load`` is a fixed point for a fully-populated ``[auth]`` block —
    the same round-trip guarantee ``test_config.py`` pins for the rest of ``HubConfig``."""
    from dataclasses import replace

    from blizzard.hub.config import AuthConfig, OAuthProviderConfig

    root = tmp_path / "hub"
    root.mkdir()
    scaffolded = HubConfig.scaffold(root)
    edited = replace(
        scaffolded,
        auth=AuthConfig(
            mode=AUTH_MODE_OAUTH,
            superuser="ada@example.com",
            oauth_providers=(
                OAuthProviderConfig(
                    name="oidc-co",
                    type="oidc",
                    display_name="Example SSO",
                    client_id="cid",
                    client_secret_env="BZ_OAUTH_OIDC_SECRET",
                    issuer="https://issuer.example.com",
                ),
            ),
        ),
    )
    (root / "blizzard-hub.toml").write_text(edited.to_toml())

    loaded = HubConfig.load(root)
    assert loaded.auth == edited.auth
