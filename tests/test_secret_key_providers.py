"""The hub secret key providers — env and directory sources (unit tier)."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from blizzard.hub.config import ConfigError
from blizzard.hub.secrets import ENV_SECRET_KEY, ENV_SECRET_KEY_PREVIOUS, hub_key_provider, secret_keys_dir
from blizzard.hub.secrets.internal.directory_keys import DirectoryKeyProvider
from blizzard.hub.secrets.internal.env_keys import EnvKeyProvider
from blizzard.hub.secrets.internal.key_material import generation_of

pytestmark = pytest.mark.unit

_MATERIAL = bytes(range(32))
_PREVIOUS_MATERIAL = bytes(range(1, 33))


def _b64(material: bytes) -> str:
    return base64.b64encode(material).decode()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_directory_provider_mints_a_generation_with_the_signing_key_layout(tmp_path: Path) -> None:
    keys_dir = tmp_path / "secret-keys"
    provider = DirectoryKeyProvider(keys_dir)

    current = provider.current()
    meta = json.loads((keys_dir / "meta.json").read_text())
    assert meta == {"current": current.key_id, "previous": None}
    assert len(current.material) == 32
    assert _mode(keys_dir) == 0o700
    assert _mode(keys_dir / "meta.json") == 0o600
    assert _mode(keys_dir / f"{current.key_id}.key") == 0o600
    assert provider.available_ids() == {current.key_id}


def test_promote_replaces_meta_whole_at_mode_0600_leaving_no_temp_file(tmp_path: Path) -> None:
    keys_dir = tmp_path / "secret-keys"
    provider = DirectoryKeyProvider(keys_dir)
    first = provider.current()
    second = provider.mint()

    provider.promote(second.key_id)

    meta = keys_dir / "meta.json"
    assert json.loads(meta.read_text()) == {"current": second.key_id, "previous": first.key_id}
    assert _mode(meta) == 0o600
    assert sorted(path.name for path in keys_dir.glob("meta.json*")) == ["meta.json"]


def test_a_reader_at_the_moment_meta_is_replaced_sees_the_previous_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys_dir = tmp_path / "secret-keys"
    provider = DirectoryKeyProvider(keys_dir)
    first = provider.current()
    second = provider.mint()
    seen: list[dict[str, str | None]] = []
    replace = os.replace

    def spying_replace(src: object, dst: object) -> None:
        seen.append(json.loads((keys_dir / "meta.json").read_text()))
        replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", spying_replace)
    provider.promote(second.key_id)

    assert seen == [{"current": first.key_id, "previous": None}]


@pytest.mark.parametrize("failing", ["fsync", "replace"])
def test_a_failed_meta_write_keeps_the_previous_meta_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    keys_dir = tmp_path / "secret-keys"
    provider = DirectoryKeyProvider(keys_dir)
    first = provider.current()
    second = provider.mint()

    def boom(*_args: object) -> None:
        raise OSError("injected")

    monkeypatch.setattr(os, failing, boom)
    with pytest.raises(OSError, match="injected"):
        provider.promote(second.key_id)
    monkeypatch.undo()

    assert json.loads((keys_dir / "meta.json").read_text()) == {"current": first.key_id, "previous": None}
    assert sorted(path.name for path in keys_dir.glob("meta.json*")) == ["meta.json"]


def test_the_directory_provider_reuses_an_existing_generation(tmp_path: Path) -> None:
    first = DirectoryKeyProvider(tmp_path / "secret-keys").current()

    assert DirectoryKeyProvider(tmp_path / "secret-keys").current() == first


def test_the_directory_provider_follows_a_generation_removed_from_disk(tmp_path: Path) -> None:
    provider = DirectoryKeyProvider(tmp_path / "secret-keys")
    key_id = provider.current().key_id

    (tmp_path / "secret-keys" / f"{key_id}.key").unlink()

    assert provider.generation(key_id) is None
    assert provider.available_ids() == frozenset()


def test_the_env_provider_holds_current_and_previous() -> None:
    provider = EnvKeyProvider({ENV_SECRET_KEY: _b64(_MATERIAL), ENV_SECRET_KEY_PREVIOUS: _b64(_PREVIOUS_MATERIAL)})

    assert provider.current() == generation_of(_MATERIAL)
    assert provider.available_ids() == {generation_of(_MATERIAL).key_id, generation_of(_PREVIOUS_MATERIAL).key_id}


@pytest.mark.parametrize(
    ("variable", "raw"),
    [
        (ENV_SECRET_KEY, "not-base64-tok-planted!!"),
        (ENV_SECRET_KEY, _b64(b"tok-planted-short")),
        (ENV_SECRET_KEY_PREVIOUS, "not-base64-tok-planted!!"),
    ],
)
def test_a_malformed_env_key_is_refused_by_name_without_its_content(variable: str, raw: str) -> None:
    environ = {ENV_SECRET_KEY: _b64(_MATERIAL), variable: raw}

    with pytest.raises(ConfigError) as caught:
        EnvKeyProvider(environ)
    assert variable in str(caught.value)
    assert raw not in str(caught.value)
    assert "tok-planted" not in str(caught.value)


def test_an_env_key_and_a_directory_key_of_the_same_material_share_a_generation(tmp_path: Path) -> None:
    keys_dir = tmp_path / "secret-keys"
    from_dir = DirectoryKeyProvider(keys_dir).current()

    from_env = EnvKeyProvider({ENV_SECRET_KEY: _b64(from_dir.material)}).current()

    assert from_env.key_id == from_dir.key_id


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
