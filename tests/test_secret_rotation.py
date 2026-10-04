"""``blizzard hub secret rotate-key`` (component tier) — a real store and key source."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import update

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.domain.secrets import SecretName, SecretRotationConflict
from blizzard.hub.secrets import (
    ENV_SECRET_KEY,
    ENV_SECRET_KEY_PREVIOUS,
    StoreSecretReader,
    hub_key_provider,
    secret_cipher,
    secret_keys_dir,
)
from blizzard.hub.secrets.rotation import rotate_keys
from blizzard.hub.store import schema
from blizzard.hub.store.internal.secret_store import SecretStore
from tests.support import OP, config_authoring, hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, tzinfo=UTC)
_KEY_A = base64.b64encode(bytes(range(32))).decode()
_KEY_B = base64.b64encode(bytes(range(32, 64))).decode()


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    monkeypatch.delenv(ENV_SECRET_KEY_PREVIOUS, raising=False)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.config: HubConfig = hub_runtime.init_environment(tmp_path / "hub")
        self.engine = create_engine_from_url(self.config.db_url)
        self.store = SecretStore(hub_store_connections(self.engine))
        self.meta = secret_keys_dir(self.config.data_dir) / "meta.json"

    def seed(self, environ: dict[str, str], **values: str) -> None:
        keys = hub_key_provider(environ, data_dir=self.config.data_dir)
        authoring = config_authoring(self.engine, keys=keys, clock=FixedClock(_NOW))
        for name, value in values.items():
            authoring.create_secret(SecretName.parse(name), value, OP)

    def reveal(self, environ: dict[str, str], name: str) -> str:
        keys = hub_key_provider(environ, data_dir=self.config.data_dir)
        reader = StoreSecretReader(catalog=self.store, sealed=self.store, cipher=secret_cipher(keys))
        return reader.reveal(SecretName.parse(name)).expose()

    def key_ids(self) -> set[str]:
        return self.store.key_ids_in_use()


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_directory_rotation_moves_every_row_and_a_hub_opened_before_still_reveals(world: _World) -> None:
    world.seed({}, **{"gh": "tok-1", "slack": "tok-2"})
    before = hub_key_provider({}, data_dir=world.config.data_dir)  # a running hub's provider
    old_id = before.current().key_id

    result = rotate_keys(world.store, {}, data_dir=world.config.data_dir)

    assert result.resealed == 2
    assert result.key_id != old_id
    assert world.key_ids() == {result.key_id}
    meta = json.loads(world.meta.read_text())
    assert meta == {"current": result.key_id, "previous": old_id}
    reader = StoreSecretReader(catalog=world.store, sealed=world.store, cipher=secret_cipher(before))
    assert reader.reveal(SecretName.parse("gh")).expose() == "tok-1"
    assert reader.reveal(SecretName.parse("slack")).expose() == "tok-2"


def test_rotation_keeps_the_revision_and_replacement_stamp(world: _World) -> None:
    world.seed({}, gh="tok-1")
    record = world.store.get("gh")

    rotate_keys(world.store, {}, data_dir=world.config.data_dir)

    assert world.store.get("gh") == record


def test_a_second_rotation_deletes_a_generation_no_row_references(world: _World) -> None:
    world.seed({}, gh="tok-1")
    first = rotate_keys(world.store, {}, data_dir=world.config.data_dir)
    second = rotate_keys(world.store, {}, data_dir=world.config.data_dir)

    files = {p.stem for p in secret_keys_dir(world.config.data_dir).glob("*.key")}
    assert files == {first.key_id, second.key_id}
    assert world.reveal({}, "gh") == "tok-1"


def test_env_rotation_reseals_under_the_current_key_using_previous(world: _World) -> None:
    world.seed({ENV_SECRET_KEY: _KEY_A}, gh="tok-1")
    env = {ENV_SECRET_KEY: _KEY_B, ENV_SECRET_KEY_PREVIOUS: _KEY_A}
    meta_before = world.meta.read_text()

    result = rotate_keys(world.store, env, data_dir=world.config.data_dir)

    assert result.resealed == 1
    assert world.key_ids() == {result.key_id}
    assert world.reveal({ENV_SECRET_KEY: _KEY_B}, "gh") == "tok-1"
    assert world.meta.read_text() == meta_before  # env mode mints and promotes nothing


def test_env_rotation_without_the_previous_key_refuses_naming_the_generation(world: _World) -> None:
    world.seed({ENV_SECRET_KEY: _KEY_A}, gh="tok-1")
    old_id = next(iter(world.key_ids()))

    with pytest.raises(ConfigError, match=old_id):
        rotate_keys(world.store, {ENV_SECRET_KEY: _KEY_B}, data_dir=world.config.data_dir)
    assert world.key_ids() == {old_id}


def test_a_concurrent_replace_rolls_the_whole_rotation_back(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.seed({}, **{"gh": "tok-1", "slack": "tok-2"})
    before = world.key_ids()
    real_list = world.store.list_sealed

    def list_then_race():  # type: ignore[no-untyped-def]
        rows = real_list()
        with world.engine.begin() as conn:  # a replace lands after the read, before the commit
            conn.execute(update(schema.secrets).where(schema.secrets.c.name == "slack").values(revision=2))
        return rows

    monkeypatch.setattr(world.store, "list_sealed", list_then_race)
    with pytest.raises(SecretRotationConflict):
        rotate_keys(world.store, {}, data_dir=world.config.data_dir)

    assert world.key_ids() == before
    assert json.loads(world.meta.read_text())["previous"] is None


def test_the_verb_runs_offline_and_a_conflict_or_config_error_exits_non_zero(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.seed({}, gh="tok-1")
    runner = CliRunner()

    ok = runner.invoke(hub_group, ["secret", "rotate-key", "--dir", str(tmp_path / "hub")])
    assert ok.exit_code == 0, ok.output
    assert "re-sealed 1 secret(s)" in ok.output

    monkeypatch.setenv(ENV_SECRET_KEY, _KEY_B)
    refused = runner.invoke(hub_group, ["secret", "rotate-key", "--dir", str(tmp_path / "hub")])
    assert refused.exit_code != 0
    assert _KEY_B not in refused.output
