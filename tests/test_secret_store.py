"""``SecretStore``, the domain services, and the store-backed reader (component tier).

Migrated-to-head sqlite-on-disk, the ``tests/test_scope_store.py`` shape. Tampering
is done in raw SQL, the way a restored or hand-edited store would carry it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, update

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.secrets import (
    SecretAlreadyExists,
    SecretAuthoring,
    SecretLifecycle,
    SecretName,
    SecretNotFound,
    SecretRetired,
    SecretRevisionConflict,
    SecretUnreadable,
    SecretValue,
)
from blizzard.hub.runtime import migration_runner
from blizzard.hub.secrets import StoreSecretReader, hub_key_provider, secret_cipher
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.schema import secrets
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
        self.engine: Engine = create_engine_from_url(db_url)
        self.store = SecretStore(hub_store_connections(self.engine))
        self.clock = FixedClock(_NOW)
        cipher = secret_cipher(hub_key_provider({}, data_dir=tmp_path / "data"))
        self.authoring = SecretAuthoring(secrets=self.store, cipher=cipher, clock=self.clock)
        self.lifecycle = SecretLifecycle(secrets=self.store, clock=self.clock)
        self.reader = StoreSecretReader(catalog=self.store, sealed=self.store, cipher=cipher)

    def create(self, name: str, value: str) -> None:
        self.authoring.create(SecretName.parse(name), SecretValue(value), by="op")

    def raw(self, name: str):  # type: ignore[no-untyped-def]
        with self.engine.connect() as conn:
            return conn.execute(select(secrets).where(secrets.c.name == name)).one()

    def overwrite(self, name: str, **values: object) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(secrets).where(secrets.c.name == name).values(**values))


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_create_stores_revision_one_and_no_plaintext(world: _World) -> None:
    world.create("gh", "tok-planted")

    record = world.store.get("gh")
    assert record is not None
    assert (record.revision, record.replaced_by, record.replaced_at, record.created_at) == (1, "op", _NOW, _NOW)
    assert "tok-planted" not in repr(tuple(world.raw("gh")))
    assert world.reader.reveal(SecretName.parse("gh")).expose() == "tok-planted"


def test_create_refuses_a_taken_name(world: _World) -> None:
    world.create("gh", "a")

    with pytest.raises(SecretAlreadyExists):
        world.create("gh", "b")


def test_replace_moves_to_the_next_revision_and_reseals(world: _World) -> None:
    world.create("gh", "old")
    first = world.raw("gh")
    world.clock.advance(timedelta(minutes=5))
    record = world.store.get("gh")
    assert record is not None

    replaced = world.authoring.replace(record, SecretValue("new"), by="alice")

    assert (replaced.revision, replaced.replaced_by, replaced.created_at) == (2, "alice", _NOW)
    assert replaced.replaced_at == _NOW + timedelta(minutes=5)
    assert world.raw("gh").nonce != first.nonce
    assert world.reader.reveal(SecretName.parse("gh")).expose() == "new"


def test_replace_from_a_stale_revision_is_a_conflict_naming_the_current_one(world: _World) -> None:
    world.create("gh", "v1")
    stale = world.store.get("gh")
    assert stale is not None
    world.authoring.replace(stale, SecretValue("v2"), by="op")

    with pytest.raises(SecretRevisionConflict) as caught:
        world.authoring.replace(stale, SecretValue("v3"), by="op")
    assert caught.value.current == 2
    assert world.reader.reveal(SecretName.parse("gh")).expose() == "v2"


def test_replace_with_a_mismatched_if_match_is_refused_before_writing(world: _World) -> None:
    world.create("gh", "v1")
    record = world.store.get("gh")
    assert record is not None

    with pytest.raises(SecretRevisionConflict) as caught:
        world.authoring.replace(record, SecretValue("v2"), by="op", if_match=7)
    assert caught.value.current == 1
    assert world.raw("gh").revision == 1


def test_a_retired_secret_refuses_replace_and_reveal_until_enabled(world: _World) -> None:
    world.create("gh", "v1")
    record = world.store.get("gh")
    assert record is not None

    world.lifecycle.retire(record, by="op")
    assert world.store.is_retired("gh")
    assert world.store.retired_names() == {"gh"}
    with pytest.raises(SecretRetired):
        world.authoring.replace(record, SecretValue("v2"), by="op")
    with pytest.raises(SecretRetired):
        world.reader.reveal(SecretName.parse("gh"))

    world.lifecycle.enable(record, by="op")
    assert world.store.retired_names() == set()
    assert world.reader.reveal(SecretName.parse("gh")).expose() == "v1"


def test_reveal_refuses_an_unknown_name(world: _World) -> None:
    with pytest.raises(SecretNotFound):
        world.reader.reveal(SecretName.parse("absent"))


def test_a_ciphertext_copied_onto_another_row_fails_to_open(world: _World) -> None:
    world.create("gh", "tok-a")
    world.create("other", "tok-b")
    source = world.raw("gh")

    world.overwrite("other", ciphertext=source.ciphertext, nonce=source.nonce, key_id=source.key_id)

    with pytest.raises(SecretUnreadable):
        world.reader.reveal(SecretName.parse("other"))


def test_a_ciphertext_restored_at_an_older_revision_fails_to_open(world: _World) -> None:
    world.create("gh", "v1")
    old = world.raw("gh")
    record = world.store.get("gh")
    assert record is not None
    world.authoring.replace(record, SecretValue("v2"), by="op")

    world.overwrite("gh", ciphertext=old.ciphertext, nonce=old.nonce)

    with pytest.raises(SecretUnreadable):
        world.reader.reveal(SecretName.parse("gh"))


def test_catalog_reads_metadata_in_bulk_and_the_key_ids_in_use(world: _World) -> None:
    world.create("a", "x")
    world.create("b", "y")

    assert set(world.store.get_many(["a", "b", "absent"])) == {"a", "b"}
    assert world.store.get_many([]) == {}
    assert [r.name for r in world.store.list_all()] == ["a", "b"]
    assert world.store.key_ids_in_use() == {world.raw("a").key_id}


def test_a_secret_name_is_a_slug() -> None:
    assert SecretName.parse("gh-token-2").value == "gh-token-2"
    with pytest.raises(ValueError, match="GH"):
        SecretName.parse("GH")
