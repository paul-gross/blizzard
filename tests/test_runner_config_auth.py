"""Runner config's federation identity + local role knobs round-trip through ``to_toml``/``load``
(issue #95) — ``public_url`` (its bare-string and multi-origin list forms, its derived
``redirect_uris``, its load-time validation; issue #287), ``[auth]``, and ``[auth.users]``."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from blizzard.runner.config import ENV_PUBLIC_URL, ConfigError, RunnerConfig

pytestmark = pytest.mark.unit


def _round_trip(tmp_path: Path, config: RunnerConfig) -> RunnerConfig:
    root = tmp_path
    root.mkdir(exist_ok=True)
    toml = config.to_toml()
    (root / "blizzard-runner.toml").write_text(
        toml.replace('db_url = "sqlite://"', f'db_url = "sqlite:///{root}/r.db"')
    )
    return RunnerConfig.load(root)


def test_public_url_round_trips(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", public_urls=("https://runner-a.example",))
    reloaded = _round_trip(tmp_path, config)
    assert reloaded.public_url == "https://runner-a.example"


def test_a_lone_origin_round_trips_as_a_bare_string_not_a_list(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", public_urls=("https://runner-a.example",))
    assert 'public_url = "https://runner-a.example"' in config.to_toml()
    assert _round_trip(tmp_path, config).public_urls == ("https://runner-a.example",)


def test_redirect_uris_derive_from_public_url() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://", public_urls=("https://runner-a.example/",))
    assert config.redirect_uris == ("https://runner-a.example/api/auth/callback",)


def test_no_public_url_means_no_redirect_uris() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://")
    assert config.public_url == ""
    assert config.redirect_uris == ()


def test_an_empty_authored_public_url_reads_as_no_identity_rather_than_failing(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text(f'db_url = "sqlite:///{tmp_path}/r.db"\npublic_url = ""\n')
    config = RunnerConfig.load(tmp_path)
    assert config.public_urls == ()
    assert config.public_url == ""
    assert config.redirect_uris == ()


_THREE = ("http://127.0.0.1:8431", "http://localhost:8431", "https://tailnet.example:8431")


def test_redirect_uris_derive_one_per_declared_origin() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://", public_urls=_THREE)
    assert config.redirect_uris == (
        "http://127.0.0.1:8431/api/auth/callback",
        "http://localhost:8431/api/auth/callback",
        "https://tailnet.example:8431/api/auth/callback",
    )


def test_the_first_declared_origin_is_the_canonical_one() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://", public_urls=_THREE)
    assert config.public_origins.canonical == "http://127.0.0.1:8431"
    assert config.public_url == "http://127.0.0.1:8431"


def test_several_origins_round_trip_as_a_list(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", public_urls=_THREE)
    assert f"public_url = [{', '.join(chr(34) + u + chr(34) for u in _THREE)}]" in config.to_toml()
    reloaded = _round_trip(tmp_path, config)
    assert reloaded.public_urls == _THREE
    assert len(reloaded.redirect_uris) == 3


def test_a_single_origin_may_be_authored_as_a_bare_string(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text(
        f'db_url = "sqlite:///{tmp_path}/r.db"\npublic_url = "https://runner-a.example"\n'
    )
    config = RunnerConfig.load(tmp_path)
    assert config.public_urls == ("https://runner-a.example",)
    assert config.redirect_uris == ("https://runner-a.example/api/auth/callback",)


def test_a_scaffold_declaring_no_origins_reads_back_empty(tmp_path: Path) -> None:
    reloaded = _round_trip(tmp_path, RunnerConfig(root=tmp_path, db_url="sqlite://"))
    assert reloaded.public_urls == ()
    assert reloaded.redirect_uris == ()


def test_a_malformed_origin_fails_at_config_load(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text(
        f'db_url = "sqlite:///{tmp_path}/r.db"\npublic_url = ["localhost:8431"]\n'
    )
    with pytest.raises(ConfigError):
        RunnerConfig.load(tmp_path)


def test_a_malformed_bare_string_origin_fails_at_config_load(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text(
        f'db_url = "sqlite:///{tmp_path}/r.db"\npublic_url = "localhost:8431"\n'
    )
    with pytest.raises(ConfigError):
        RunnerConfig.load(tmp_path)


def test_origins_declaring_one_authority_twice_fails_at_config_load(tmp_path: Path) -> None:
    (tmp_path / "blizzard-runner.toml").write_text(
        f'db_url = "sqlite:///{tmp_path}/r.db"\npublic_url = ["https://t.example:8431", "https://T.Example:8431"]\n'
    )
    with pytest.raises(ConfigError):
        RunnerConfig.load(tmp_path)


def test_several_origins_are_settable_by_env_var_for_a_fresh_scaffold(tmp_path: Path) -> None:
    with mock.patch.dict(os.environ, {ENV_PUBLIC_URL: ", ".join(_THREE)}, clear=False):
        config = RunnerConfig.scaffold(tmp_path)
    assert config.public_urls == _THREE
    assert len(config.redirect_uris) == 3


def test_one_env_declared_origin_still_works(tmp_path: Path) -> None:
    with mock.patch.dict(os.environ, {ENV_PUBLIC_URL: "https://runner-a.example"}, clear=False):
        config = RunnerConfig.scaffold(tmp_path)
    assert config.public_urls == ("https://runner-a.example",)


def test_a_malformed_env_declared_origin_fails_rather_than_being_dropped(tmp_path: Path) -> None:
    with (
        mock.patch.dict(os.environ, {ENV_PUBLIC_URL: "localhost:8431"}, clear=False),
        pytest.raises(ConfigError),
    ):
        RunnerConfig.scaffold(tmp_path)


def test_auth_block_round_trips(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        auth_superuser="root-op",
        auth_hub_role_default="guest",
        auth_users=(("alice", "admin"), ("bob", "contributor")),
    )
    reloaded = _round_trip(tmp_path, config)
    assert reloaded.auth_superuser == "root-op"
    assert reloaded.auth_hub_role_default == "guest"
    assert set(reloaded.auth_users) == {("alice", "admin"), ("bob", "contributor")}


def test_auth_defaults_round_trip_on_a_fresh_scaffold(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    reloaded = _round_trip(tmp_path, config)
    assert reloaded.auth_superuser is None
    assert reloaded.auth_hub_role_default == "mirror"
    assert reloaded.auth_users == ()


# --- Model / effort tier aliases (issue #144) --------------------------------


def test_model_and_effort_aliases_round_trip(tmp_path: Path) -> None:
    """`runner init` has to scaffold these and `load` has to read them back, or an
    operator's tier table is written and silently ignored."""
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        model_aliases=(("blizzard:basic", "haiku"), ("blizzard:advanced", "claude-opus-5")),
        effort_aliases=(("max", "xhigh"),),
    )

    reloaded = _round_trip(tmp_path, config)

    assert reloaded.model_aliases == (("blizzard:basic", "haiku"), ("blizzard:advanced", "claude-opus-5"))
    assert reloaded.effort_aliases == (("max", "xhigh"),)


def test_a_scaffold_declaring_no_aliases_reads_back_empty(tmp_path: Path) -> None:
    # The zero-config runner: the adapter's own built-in tier defaults stand.
    reloaded = _round_trip(tmp_path, RunnerConfig(root=tmp_path, db_url="sqlite://"))

    assert reloaded.model_aliases == ()
    assert reloaded.effort_aliases == ()
