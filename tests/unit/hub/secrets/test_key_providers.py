"""The hub's key-source binding — env wins, else the directory (unit tier)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from blizzard.foundation.secrets.internal.key_material import generation_of
from blizzard.hub.config import ConfigError
from blizzard.hub.secrets import ENV_SECRET_KEY, ENV_SECRET_KEY_PREVIOUS, hub_key_provider, secret_keys_dir

_MATERIAL = bytes(range(32))


def _b64(material: bytes) -> str:
    return base64.b64encode(material).decode()


def test_the_env_source_wins_and_mints_nothing_on_disk(tmp_path: Path) -> None:
    provider = hub_key_provider({ENV_SECRET_KEY: _b64(_MATERIAL)}, data_dir=tmp_path)

    assert provider.current() == generation_of(_MATERIAL)
    assert not secret_keys_dir(tmp_path).exists()


def test_without_the_env_key_the_directory_source_mints(tmp_path: Path) -> None:
    provider = hub_key_provider({}, data_dir=tmp_path)

    assert (secret_keys_dir(tmp_path) / f"{provider.current().key_id}.key").is_file()


def test_a_previous_env_key_without_a_current_one_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=ENV_SECRET_KEY_PREVIOUS):
        hub_key_provider({ENV_SECRET_KEY_PREVIOUS: _b64(_MATERIAL)}, data_dir=tmp_path)
