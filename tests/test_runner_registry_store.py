"""The fleet-registry store (``RunnerRegistryStore``) at its own seam — adding a runner, its first and
later registrations, the heartbeat, the batched name read, the revoked-hash lookup, and the listing
order."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRegistration, TokenRotation
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import count_queries, hub_store_connections

pytestmark = pytest.mark.component

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_LATER = _T0 + timedelta(minutes=5)


def _engine(tmp_path: Path) -> sa.Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return create_engine_from_url(db_url)


def _store(tmp_path: Path) -> RunnerRegistryStore:
    return RunnerRegistryStore(hub_store_connections(_engine(tmp_path)))


def _add(store: RunnerRegistryStore, runner_id: str, name: str = "r-claude", *, at: datetime = _T0) -> None:
    store.add(RunnerAddition(runner_id, name, f"hash-{runner_id}", at=at, by="admin"))


def _get(store: RunnerRegistryStore, runner_id: str) -> RunnerRegistration:
    registration = store.get_runner(runner_id)
    assert registration is not None
    return registration


def test_an_added_runner_holds_its_name_and_token_and_is_never_connected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_1")

    added = _get(store, "rn_1")
    assert (added.name, added.added_at, added.added_by, added.token_hash) == ("r-claude", _T0, "admin", "hash-rn_1")
    assert (added.workspace_id, added.registered_at, added.last_seen_at) == (None, None, None)
    assert added.never_connected()
    # Its token already resolves — that is how its first registration authenticates.
    resolved = store.registration_for_token_hash("hash-rn_1")
    assert resolved is not None and resolved.runner_id == "rn_1"


def test_adding_an_id_twice_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_1")

    with pytest.raises(HubStoreError):
        _add(store, "rn_1", "another")
    assert _get(store, "rn_1").name == "r-claude"


def test_the_first_registration_stamps_registered_at_and_later_ones_refresh_in_place(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_1")

    assert store.record_registration("rn_1", workspace_id="w1", env_capacity=2, at=_T0) is True
    assert store.record_registration("rn_1", workspace_id="w2", env_capacity=3, at=_LATER) is False

    registration = _get(store, "rn_1")
    assert (registration.registered_at, registration.last_seen_at) == (_T0, _LATER)
    assert (registration.workspace_id, registration.env_capacity) == ("w2", 3)
    assert not registration.never_connected()


def test_a_declared_name_replaces_the_held_one_and_no_name_keeps_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_1", "runner-local")

    store.record_registration("rn_1", name="r-claude", workspace_id="w1", env_capacity=None, at=_T0)
    assert _get(store, "rn_1").name == "r-claude"

    store.record_registration("rn_1", workspace_id="w1", env_capacity=None, at=_LATER)
    assert _get(store, "rn_1").name == "r-claude"


def test_registering_a_runner_never_added_raises_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(LookupError):
        store.record_registration("rn_ghost", workspace_id="w1", env_capacity=None, at=_T0)
    assert store.get_runner("rn_ghost") is None


def test_a_heartbeat_refreshes_only_a_registered_runner(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_live")
    _add(store, "rn_new")
    store.record_registration("rn_live", workspace_id="w1", env_capacity=None, at=_T0)

    assert store.touch_last_seen("rn_live", at=_LATER) is True
    assert store.touch_last_seen("rn_new", at=_LATER) is False
    assert store.touch_last_seen("rn_ghost", at=_LATER) is False

    assert _get(store, "rn_live").last_seen_at == _LATER
    assert _get(store, "rn_new").last_seen_at is None


def test_names_for_answers_every_known_id_in_one_query(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = RunnerRegistryStore(hub_store_connections(engine))
    _add(store, "rn_1", "twin")
    _add(store, "rn_2", "twin")
    names: dict[str, str] = {}

    def read() -> None:
        names.update(store.names_for(["rn_1", "rn_2", "rn_ghost", "rn_1"]))

    assert count_queries(engine, read) == 1
    assert names == {"rn_1": "twin", "rn_2": "twin"}
    assert count_queries(engine, lambda: store.names_for([])) == 0


def test_a_revoked_hash_names_the_runner_it_was_issued_to(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_rotated")
    _add(store, "rn_revoked")
    store.rotate_token(TokenRotation("rn_rotated", "hash-current", at=_T0, by="admin"))
    store.revoke_token("rn_revoked", at=_T0, by="admin")  # as retiring a runner does

    assert store.revoked_token_runner_id("hash-rn_rotated") == "rn_rotated"
    assert store.revoked_token_runner_id("hash-rn_revoked") == "rn_revoked"
    assert store.revoked_token_runner_id("hash-current") is None
    assert store.revoked_token_runner_id("hash-never-issued") is None


def test_runners_list_oldest_added_first_with_the_id_breaking_a_tie(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, "rn_b")
    _add(store, "rn_c", at=_T0 - timedelta(seconds=1))
    _add(store, "rn_a")
    store.record_registration("rn_c", workspace_id="w1", env_capacity=None, at=_LATER)

    assert [r.runner_id for r in store.list_runners()] == ["rn_c", "rn_a", "rn_b"]
