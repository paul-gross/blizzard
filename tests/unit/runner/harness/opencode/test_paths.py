"""``src/blizzard/runner/harness/opencode/paths.py`` — the OpenCode credential, store, and config-directory resolvers (unit)."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.harness.opencode.paths import (
    resolve_opencode_auth_path,
    resolve_opencode_config_dir,
    resolve_opencode_store_path,
)


def test_resolve_opencode_auth_path_uses_xdg_data_home_when_set() -> None:
    env = {"XDG_DATA_HOME": "/custom/data", "HOME": "/home/operator"}
    assert resolve_opencode_auth_path(env) == Path("/custom/data/opencode/auth.json")


def test_resolve_opencode_auth_path_treats_an_empty_xdg_data_home_as_unset() -> None:
    env = {"XDG_DATA_HOME": "", "HOME": "/home/operator"}
    assert resolve_opencode_auth_path(env) == Path("/home/operator/.local/share/opencode/auth.json")


def test_resolve_opencode_auth_path_falls_back_to_home() -> None:
    assert resolve_opencode_auth_path({"HOME": "/home/operator"}) == Path(
        "/home/operator/.local/share/opencode/auth.json"
    )


def test_resolve_opencode_auth_path_is_none_with_neither_variable() -> None:
    assert resolve_opencode_auth_path({}) is None


def test_resolve_opencode_config_dir_uses_xdg_config_home_when_set() -> None:
    env = {"XDG_CONFIG_HOME": "/custom/config", "HOME": "/home/operator"}
    assert resolve_opencode_config_dir(env) == Path("/custom/config/opencode")


def test_resolve_opencode_config_dir_treats_an_empty_xdg_config_home_as_unset() -> None:
    env = {"XDG_CONFIG_HOME": "", "HOME": "/home/operator"}
    assert resolve_opencode_config_dir(env) == Path("/home/operator/.config/opencode")


def test_resolve_opencode_config_dir_is_none_with_neither_variable() -> None:
    assert resolve_opencode_config_dir({}) is None


def test_the_store_sits_beside_the_credential_document() -> None:
    assert resolve_opencode_store_path({"XDG_DATA_HOME": "/custom/data"}) == Path("/custom/data/opencode/opencode.db")
    assert resolve_opencode_store_path({"HOME": "/home/operator"}) == Path(
        "/home/operator/.local/share/opencode/opencode.db"
    )
    assert resolve_opencode_store_path({}) is None
