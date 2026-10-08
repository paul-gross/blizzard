"""``src/blizzard/runner/harness/opencode/paths.py`` — the one OpenCode auth-path resolver (unit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.harness.opencode.paths import resolve_opencode_auth_path

pytestmark = pytest.mark.unit


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
