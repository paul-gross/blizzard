"""``[auth]`` config parsing — ``mode``/``superuser`` validated,
``[[auth.oauth.provider]]`` structurally parsed-and-carried (semantic validation is #92's).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH, ConfigError, HubConfig
from tests.config_inputs import load_hub_config

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
    config = load_hub_config(root)
    assert config.auth.mode == AUTH_MODE_NONE


def test_oauth_mode_and_superuser_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, '[auth]\nmode = "oauth"\nsuperuser = "ada@example.com"\n')
    config = load_hub_config(root)
    assert config.auth.mode == AUTH_MODE_OAUTH
    assert config.auth.superuser == "ada@example.com"


def test_unknown_auth_mode_rejected(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, '[auth]\nmode = "bogus"\n')
    with pytest.raises(ConfigError, match="auth\\.mode"):
        load_hub_config(root)


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
    config = load_hub_config(root)
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
        load_hub_config(root)


def test_oauth_provider_api_base_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(
        root,
        "[[auth.oauth.provider]]\n"
        'name = "github"\n'
        'type = "github"\n'
        'display_name = "GitHub"\n'
        'client_id = "abc123"\n'
        'client_secret_env = "BZ_OAUTH_GITHUB_SECRET"\n'
        'api_base = "http://127.0.0.1:9999"\n',
    )
    config = load_hub_config(root)
    assert config.auth.oauth_providers[0].api_base == "http://127.0.0.1:9999"


def test_oauth_provider_api_base_defaults_to_none(tmp_path: Path) -> None:
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
    config = load_hub_config(root)
    assert config.auth.oauth_providers[0].api_base is None


_OIDC_ENTRY = (
    "[[auth.oauth.provider]]\n"
    'name = "oidc-co"\n'
    'type = "oidc"\n'
    'display_name = "Example SSO"\n'
    'client_id = "cid"\n'
    'client_secret_env = "BZ_OAUTH_OIDC_SECRET"\n'
    'issuer = "https://issuer.example.com"\n'
)


def test_oauth_provider_emails_authoritative_defaults_to_false(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, _OIDC_ENTRY)
    config = load_hub_config(root)
    assert config.auth.oauth_providers[0].emails_authoritative is False


def test_an_oidc_provider_may_be_configured_authoritative_for_its_emails(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    _write(root, _OIDC_ENTRY + "emails_authoritative = true\n")
    config = load_hub_config(root)
    assert config.auth.oauth_providers[0].emails_authoritative is True


@pytest.mark.parametrize("value", ['"true"', "1"])
def test_oauth_provider_emails_authoritative_must_be_a_boolean(tmp_path: Path, value: str) -> None:
    root = tmp_path / "hub"
    _write(root, _OIDC_ENTRY + f"emails_authoritative = {value}\n")
    with pytest.raises(ConfigError, match=r"'oidc-co' has emails_authoritative=.*must be a boolean"):
        load_hub_config(root)


@pytest.mark.parametrize("value", ["true", "false"])
def test_a_github_provider_refuses_emails_authoritative(tmp_path: Path, value: str) -> None:
    root = tmp_path / "hub"
    _write(
        root,
        "[[auth.oauth.provider]]\n"
        'name = "github"\n'
        'type = "github"\n'
        'display_name = "GitHub"\n'
        'client_id = "abc123"\n'
        'client_secret_env = "BZ_OAUTH_GITHUB_SECRET"\n'
        f"emails_authoritative = {value}\n",
    )
    with pytest.raises(ConfigError, match=r"'github' is type 'github'.*emails_authoritative is an oidc key"):
        load_hub_config(root)


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
        load_hub_config(root)


def test_to_toml_round_trips_through_load(tmp_path: Path) -> None:
    """``to_toml`` → ``load`` is a fixed point for a fully-populated ``[auth]`` block —
    the same round-trip guarantee ``tests/test_config.py`` pins for the rest of ``HubConfig``."""
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
                    emails_authoritative=True,
                ),
            ),
        ),
    )
    (root / "blizzard-hub.toml").write_text(edited.to_toml())

    loaded = load_hub_config(root)
    assert loaded.auth == edited.auth
