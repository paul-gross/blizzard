"""A runner's sealed hub token binds its runner id and revision (unit tier)."""

from __future__ import annotations

import base64
import dataclasses
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.secrets.sealing import SealedValue, SecretUnreadable
from blizzard.foundation.secrets.value import SecretValue, SecretValueBlank
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_keys import ENV_HOST_SECRET_KEY, host_key_provider
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.sealed_tokens import HubTokenRetired, HubTokens, SealedHubToken, sealed_name

_AT = datetime(2026, 11, 18, tzinfo=UTC)
_KEY = base64.b64encode(bytes(range(32))).decode()


class _Rows:
    """An in-memory hub-token repository."""

    def __init__(self) -> None:
        self.rows: dict[str, SealedHubToken] = {}

    def sealed(self, runner_id: str) -> SealedHubToken | None:
        return self.rows.get(runner_id)

    def seal_if_absent(self, runner_id: str, sealed: SealedValue, *, at: datetime) -> bool:
        if runner_id in self.rows:
            return False
        self.rows[runner_id] = SealedHubToken(runner_id, 1, sealed, at)
        return True

    def replace(self, runner_id: str, seal: Callable[[int], SealedValue], *, at: datetime) -> int:
        held = self.rows.get(runner_id)
        revision = 1 if held is None else held.revision + 1
        self.rows[runner_id] = SealedHubToken(runner_id, revision, seal(revision), at)
        return revision

    def drop(self, runner_id: str) -> bool:
        return self.rows.pop(runner_id, None) is not None


def _tokens(tmp_path: Path) -> tuple[HubTokens, _Rows]:
    keys = host_key_provider({ENV_HOST_SECRET_KEY: _KEY}, HostLayout(tmp_path), mint=True)
    assert keys is not None
    rows = _Rows()
    return HubTokens(rows, secret_cipher(keys), FixedClock(_AT)), rows


def test_a_sealed_token_opens_only_under_its_runner_and_revision(tmp_path: Path) -> None:
    tokens, rows = _tokens(tmp_path)
    assert tokens.seal_if_absent("rn_a", SecretValue("tok-a"))
    opened = tokens.open("rn_a")
    assert opened is not None
    assert (opened.value.expose(), opened.revision) == ("tok-a", 1)

    rows.rows["rn_b"] = dataclasses.replace(rows.rows["rn_a"], runner_id="rn_b")
    with pytest.raises(SecretUnreadable) as moved:
        tokens.open("rn_b")
    assert moved.value.name == sealed_name("rn_b")

    rows.rows["rn_a"] = dataclasses.replace(rows.rows["rn_a"], revision=2)
    with pytest.raises(SecretUnreadable):
        tokens.open("rn_a")


def test_seal_if_absent_never_overwrites(tmp_path: Path) -> None:
    tokens, _ = _tokens(tmp_path)
    assert tokens.seal_if_absent("rn_a", SecretValue("first"))
    assert not tokens.seal_if_absent("rn_a", SecretValue("second"))
    opened = tokens.open("rn_a")
    assert opened is not None and opened.value.expose() == "first"


def test_replace_seals_at_the_next_revision_and_refuses_blank_and_retired(tmp_path: Path) -> None:
    tokens, rows = _tokens(tmp_path)
    assert tokens.open("rn_a") is None
    assert tokens.replace("rn_a", "one", retired=False) == 1
    assert tokens.replace("rn_a", "two", retired=False) == 2
    opened = tokens.open("rn_a")
    assert opened is not None and (opened.value.expose(), opened.revision) == ("two", 2)
    with pytest.raises(SecretValueBlank):
        tokens.replace("rn_a", "  ", retired=False)
    with pytest.raises(HubTokenRetired):
        tokens.replace("rn_a", "three", retired=True)
    assert rows.rows["rn_a"].revision == 2


def test_the_host_key_variable_wins_over_the_directory(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    keys = host_key_provider({ENV_HOST_SECRET_KEY: _KEY}, layout, mint=True)
    assert keys is not None
    keys.current()
    assert not layout.secret_keys_dir.exists()


def test_a_read_only_lookup_never_mints_and_a_sealing_one_mints_owner_only(tmp_path: Path) -> None:
    layout = HostLayout(tmp_path)
    assert host_key_provider({}, layout, mint=False) is None
    assert not layout.secret_keys_dir.exists()
    minted = host_key_provider({}, layout, mint=True)
    assert minted is not None
    current = minted.current()
    assert stat.S_IMODE(layout.secret_keys_dir.stat().st_mode) == 0o700
    for path in layout.secret_keys_dir.iterdir():
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    found = host_key_provider({}, layout, mint=False)
    assert found is not None and found.current().key_id == current.key_id


def test_a_malformed_host_key_variable_refuses(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        keys = host_key_provider({ENV_HOST_SECRET_KEY: "not-a-key"}, HostLayout(tmp_path), mint=True)
        assert keys is not None
        keys.current()
