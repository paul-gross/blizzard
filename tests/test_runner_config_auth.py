"""Runner config's federation identity + local role knobs round-trip through
``to_toml``/``load`` (issue #95) — ``public_url`` (and its derived ``redirect_uris``),
``[auth].superuser``/``hub_role_default``, and ``[auth.users]``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig

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
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", public_url="https://runner-a.example")
    reloaded = _round_trip(tmp_path, config)
    assert reloaded.public_url == "https://runner-a.example"


def test_redirect_uris_derive_from_public_url() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://", public_url="https://runner-a.example/")
    assert config.redirect_uris == ("https://runner-a.example/api/auth/callback",)


def test_no_public_url_means_no_redirect_uris() -> None:
    config = RunnerConfig(root=Path("."), db_url="sqlite://")
    assert config.public_url == ""
    assert config.redirect_uris == ()


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
