"""The ``20261006_1200_runner_minted_ids`` revision against a store that already holds runners — every
registration is keyed by a minted ``rn_`` id and named after its old id, every runner-id column follows
it, enrolled and revoked tokens keep resolving, the hub's own executor rows and unregistered ids stay
put, and a downgrade restores the old ids (the ``tests/test_decision_origin_migration.py`` shape)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.ids import Id
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.domain.runners.registration import RunnerAddition
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import hub_store_connections, migrate_to

pytestmark = pytest.mark.component

_BEFORE = "20261006_0900_config_import_facts"  # the head just before this revision
_REVISION = "20261006_1200_runner_minted_ids"
_T0 = datetime(2026, 7, 15, 0, 45, 16, 12000, tzinfo=UTC)
_T1 = _T0 + timedelta(days=80)

_ENROLLED = "runner-local"
_REVOKED = "r-revoked"
_RETIRED = "r-retired"
_PAUSED = "r-paused"
_ORPHAN = "r-orphan"  # holds facts but was never registered
_HUB = "hub"  # the hub's own executor id on hub-node lease and transition rows

# Old id → (registered_at, current token hash); `_REVOKED` and `_RETIRED` share an instant.
_RUNNERS: dict[str, tuple[datetime, str | None]] = {
    _ENROLLED: (_T0, "hash-enrolled"),
    _REVOKED: (_T1, None),
    _RETIRED: (_T1, None),
    _PAUSED: (_T1 + timedelta(seconds=1), "hash-paused"),
}
_REVOKED_HASHES = {_REVOKED: "hash-revoked", _RETIRED: "hash-retired"}
_SENTINEL_TABLES = frozenset({"lease_facts", "transitions"})


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'hub.db'}"


def _runner_id_columns(conn: sa.Connection) -> list[tuple[str, str]]:
    """Every column holding a runner id, found by name rather than taken from the revision's own list."""
    inspector = sa.inspect(conn)
    return sorted(
        (table, str(column["name"]))
        for table in inspector.get_table_names()
        for column in inspector.get_columns(table)
        if str(column["name"]).endswith("runner_id") and table != "runner_registrations"
    )


def _seed_row(conn: sa.Connection, table_name: str, values: dict[str, object], *, n: int) -> None:
    """Insert ``values`` into ``table_name``, filling every other required column with a value unique to
    ``n`` — the revision reads nothing in these rows but their runner id."""
    table = sa.Table(table_name, sa.MetaData(), autoload_with=conn)
    key = list(table.primary_key.columns)
    autoincremented = len(key) == 1 and isinstance(key[0].type, sa.Integer)
    row = dict(values)
    for column in table.columns:
        if column.name in row or column.nullable or column.server_default is not None:
            continue
        if column.primary_key and autoincremented:
            continue
        if isinstance(column.type, sa.Boolean):
            row[column.name] = False
        elif isinstance(column.type, sa.Integer | sa.Numeric):
            row[column.name] = n
        elif isinstance(column.type, sa.DateTime | sa.Date):
            row[column.name] = _T0
        elif isinstance(column.type, sa.LargeBinary):
            row[column.name] = b""
        else:
            row[column.name] = f"{column.name}-{n}"
    conn.execute(table.insert().values(row))


def _seed(tmp_path: Path) -> tuple[MigrationRunner, list[tuple[str, str]]]:
    """A pre-revision store: the four runners, one row per runner-id column naming the enrolled runner
    (and the orphan, where no foreign key binds it), the hub's own rows, and three work items. Returns
    the migration runner to upgrade it with, and the runner-id columns it found."""
    runner, engine = migrate_to(tmp_path, _BEFORE)
    n = 0
    try:
        with engine.begin() as conn:
            for runner_id, (registered_at, token_hash) in _RUNNERS.items():
                n += 1
                _seed_row(
                    conn,
                    "runner_registrations",
                    {
                        "runner_id": runner_id,
                        "workspace_id": "w1",
                        "registered_at": registered_at,
                        "last_seen_at": registered_at,
                        "token_hash": token_hash,
                    },
                    n=n,
                )
            for runner_id, revoked_hash in _REVOKED_HASHES.items():
                n += 1
                _seed_row(conn, "runner_token_revocations", {"runner_id": runner_id, "token_hash": revoked_hash}, n=n)
            _seed_row(conn, "runner_lifecycle_facts", {"runner_id": _RETIRED, "retired": True, "set_at": _T1}, n=n + 1)
            _seed_row(conn, "runner_pause_facts", {"runner_id": _PAUSED, "paused": True, "set_at": _T1}, n=n + 2)
            n += 2
            columns = _runner_id_columns(conn)
            foreign_keyed = {
                (table, column)
                for table, column in columns
                for fk in sa.inspect(conn).get_foreign_keys(table)
                if column in fk["constrained_columns"]
            }
            for table, column in columns:
                holders = [_ENROLLED] if (table, column) in foreign_keyed else [_ENROLLED, _ORPHAN]
                holders += [_HUB] if table in _SENTINEL_TABLES else []
                for runner_id in holders:
                    n += 1
                    _seed_row(conn, table, {column: runner_id}, n=n)
            _seed_row(conn, "epoch_owners", {"runner_id": None}, n=n + 1)
            _seed_row(conn, "event_log", {"runner_id": None}, n=n + 2)
            for work_item_id, kind, payload in (
                ("wi_fleet", "fleet", {"runner_id": _ENROLLED, "chunk_id": "ch_1", "node_name": "build"}),
                ("wi_orphan", "fleet", {"runner_id": _ORPHAN, "chunk_id": "ch_2", "node_name": "build"}),
                ("wi_user", "user", {"user_id": _ENROLLED}),
            ):
                n += 1
                _seed_row(
                    conn,
                    "work_items",
                    {"work_item_id": work_item_id, "author_kind": kind, "author_payload": json.dumps(payload)},
                    n=n,
                )
    finally:
        engine.dispose()
    return runner, columns


type _Picture = tuple[dict[tuple[str, str], Counter[str | None]], list[tuple[str, str | None]], dict[str, str]]


def _picture(tmp_path: Path, columns: list[tuple[str, str]]) -> _Picture:
    """Every runner-id column's values (as a multiset), the registrations' ids and token hashes, and
    every work item's raw author payload."""
    engine = create_engine_from_url(_db_url(tmp_path))
    try:
        with engine.connect() as conn:
            values = {
                (table, column): Counter[str | None](
                    conn.execute(sa.text(f'SELECT "{column}" FROM "{table}"')).scalars()
                )
                for table, column in columns
            }
            registrations = sorted(
                (r.runner_id, r.token_hash)
                for r in conn.execute(sa.text("SELECT runner_id, token_hash FROM runner_registrations"))
            )
            payloads = {
                r.work_item_id: r.author_payload
                for r in conn.execute(sa.text("SELECT work_item_id, author_payload FROM work_items"))
            }
            return values, registrations, payloads
    finally:
        engine.dispose()


class _FrozenRegistry:
    """The runner registry as this revision left it, read with literal SQL: the live adapter's
    statements follow today's schema, whose runner tables keep changing shape after this revision."""

    def __init__(self, tmp_path: Path) -> None:
        self._db_url = _db_url(tmp_path)

    def _rows(self, statement: str, **params: object) -> list[sa.Row]:
        engine = create_engine_from_url(self._db_url)
        try:
            with engine.connect() as conn:
                return list(conn.execute(sa.text(statement), params))
        finally:
            engine.dispose()

    def minted(self) -> dict[str, str]:
        return {r.name: r.runner_id for r in self._rows("SELECT name, runner_id FROM runner_registrations")}

    def registration(self, runner_id: str) -> sa.Row | None:
        rows = self._rows(
            "SELECT name, added_at, added_by, registered_at FROM runner_registrations WHERE runner_id = :runner_id",
            runner_id=runner_id,
        )
        return rows[0] if rows else None

    def runner_for_token(self, token_hash: str) -> str | None:
        rows = self._rows("SELECT runner_id FROM runner_registrations WHERE token_hash = :h", h=token_hash)
        return rows[0].runner_id if rows else None

    def runner_for_revoked_token(self, token_hash: str) -> str | None:
        rows = self._rows("SELECT runner_id FROM runner_token_revocations WHERE token_hash = :h", h=token_hash)
        return rows[0].runner_id if rows else None

    def newest(self, table: str, column: str, runner_id: str) -> bool:
        """The newest fact's flag in ``table`` for ``runner_id``; ``False`` when it has none."""
        rows = self._rows(
            f'SELECT "{column}" FROM "{table}" WHERE runner_id = :runner_id ORDER BY id DESC LIMIT 1',
            runner_id=runner_id,
        )
        return bool(rows[0][0]) if rows else False


def _minted(tmp_path: Path) -> dict[str, str]:
    """Each runner's old id (now its name) → its minted id."""
    return _FrozenRegistry(tmp_path).minted()


def _registrations_columns(tmp_path: Path) -> dict[str, bool]:
    engine = create_engine_from_url(_db_url(tmp_path))
    try:
        return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(engine).get_columns("runner_registrations")}
    finally:
        engine.dispose()


def test_every_runner_is_keyed_by_a_minted_id_named_after_its_old_one(tmp_path: Path) -> None:
    runner, _columns = _seed(tmp_path)
    runner.upgrade(_REVISION)

    minted = _minted(tmp_path)
    assert set(minted) == set(_RUNNERS)
    for old_id, new_id in minted.items():
        parsed = Id.parse(new_id)
        assert parsed is not None and parsed.prefix == "rn"
        assert parsed.minted_at == _RUNNERS[old_id][0]  # each instant is whole milliseconds
    # Minted in (registered_at, old id) order — the two sharing an instant included.
    assert sorted(minted, key=minted.__getitem__) == sorted(_RUNNERS, key=lambda old_id: (_RUNNERS[old_id][0], old_id))

    registry = _FrozenRegistry(tmp_path)
    for old_id, runner_id in minted.items():
        registration = registry.registration(runner_id)
        assert registration is not None
        assert registration.name == old_id
        assert registration.added_at == registration.registered_at
        assert registration.added_by is None
        assert registration.registered_at is not None  # connected, not merely added


def test_tokens_brakes_and_retirement_follow_the_minted_id(tmp_path: Path) -> None:
    runner, _columns = _seed(tmp_path)
    runner.upgrade(_REVISION)
    minted = _minted(tmp_path)
    registry = _FrozenRegistry(tmp_path)

    for old_id, (_registered_at, token_hash) in _RUNNERS.items():
        if token_hash is not None:
            assert registry.runner_for_token(token_hash) == minted[old_id]
    for old_id, revoked_hash in _REVOKED_HASHES.items():
        assert registry.runner_for_revoked_token(revoked_hash) == minted[old_id]
    assert registry.newest("runner_pause_facts", "paused", minted[_PAUSED])
    retired = {old_id for old_id in _RUNNERS if registry.newest("runner_lifecycle_facts", "retired", minted[old_id])}
    assert retired == {_RETIRED}


def test_every_runner_id_column_follows_except_the_hubs_rows_and_unregistered_ids(tmp_path: Path) -> None:
    runner, columns = _seed(tmp_path)
    before, _registrations, payloads_before = _picture(tmp_path, columns)
    runner.upgrade(_REVISION)
    after, _registrations, payloads = _picture(tmp_path, columns)
    minted = _minted(tmp_path)

    def moved(table: str, old: str | None) -> str | None:
        if old is None or (old == _HUB and table in _SENTINEL_TABLES):
            return old
        return minted.get(old, old)

    for table, column in columns:
        expected = Counter[str | None]({moved(table, old): count for old, count in before[table, column].items()})
        assert after[table, column] == expected, (table, column)
        assert minted[_ENROLLED] in expected, (table, column)
    fleet_author = {"runner_id": minted[_ENROLLED], "chunk_id": "ch_1", "node_name": "build"}
    assert payloads == {**payloads_before, "wi_fleet": json.dumps(fleet_author)}


def test_a_runner_registered_as_hub_moves_but_the_hubs_own_executor_rows_stay_put(tmp_path: Path) -> None:
    """A registration keyed ``hub`` takes a minted id and the name ``hub``, and its other rows follow
    it, while the lease and transition rows naming the hub's own executor keep reading ``hub``."""
    tables = (*sorted(_SENTINEL_TABLES), "usage_facts", "epoch_owners")
    runner, engine = migrate_to(tmp_path, _BEFORE)
    try:
        with engine.begin() as conn:
            _seed_row(
                conn,
                "runner_registrations",
                {
                    "runner_id": _HUB,
                    "workspace_id": "w1",
                    "registered_at": _T0,
                    "last_seen_at": _T0,
                    "token_hash": "hash-hub",
                },
                n=1,
            )
            for n, table in enumerate(tables, start=2):
                _seed_row(conn, table, {"runner_id": _HUB}, n=n)
    finally:
        engine.dispose()
    runner.upgrade(_REVISION)

    minted = _minted(tmp_path)
    assert set(minted) == {_HUB} and minted[_HUB].startswith("rn_")
    values, _registrations, _payloads = _picture(tmp_path, [(table, "runner_id") for table in tables])
    for table in tables:
        holder = _HUB if table in _SENTINEL_TABLES else minted[_HUB]
        assert values[table, "runner_id"] == Counter[str | None]([holder]), table


def test_downgrade_restores_the_old_ids_and_a_second_upgrade_mints_the_same_ones(tmp_path: Path) -> None:
    runner, columns = _seed(tmp_path)
    before = _picture(tmp_path, columns)
    runner.upgrade(_REVISION)
    after = _picture(tmp_path, columns)

    runner.downgrade(_BEFORE)
    shape = _registrations_columns(tmp_path)
    assert {"name", "added_at", "added_by"}.isdisjoint(shape)
    assert not any(shape[c] for c in ("workspace_id", "registered_at", "last_seen_at"))
    assert _picture(tmp_path, columns) == before

    runner.upgrade(_REVISION)
    assert _picture(tmp_path, columns) == after


def test_downgrade_keys_runners_added_after_the_upgrade_by_their_unshared_names(tmp_path: Path) -> None:
    """Lossy by design: a name two runners share maps back to neither, and a runner added but never
    connected reads as registered when it was added, in an empty workspace."""
    runner, engine = migrate_to(tmp_path, "head")
    engine.dispose()
    # A scoped write refuses a tenant the store does not hold, so the registry writes as the store's own.
    registry = RunnerRegistryStore(hub_store_connections(create_engine_from_url(_db_url(tmp_path))))
    for runner_id, name in (("rn_A", "solo"), ("rn_B", "twin"), ("rn_C", "twin"), ("rn_D", "rn_A")):
        registry.add(RunnerAddition(runner_id, name, f"hash-{runner_id}", at=_T0, by="admin"))
    for runner_id in ("rn_A", "rn_B", "rn_C"):
        registry.record_registration(runner_id, workspace_id="w1", env_capacity=None, at=_T1)

    runner.downgrade(_BEFORE)

    engine = create_engine_from_url(_db_url(tmp_path))
    try:
        with engine.connect() as conn:
            rows = {
                r.runner_id: (r.token_hash, r.workspace_id, r.registered_at)
                for r in conn.execute(sa.text("SELECT * FROM runner_registrations"))
            }
    finally:
        engine.dispose()
    assert set(rows) == {"solo", "rn_B", "rn_C", "rn_D"}
    assert rows["solo"][0] == "hash-rn_A"
    assert rows["rn_D"][:2] == ("hash-rn_D", "")  # never connected; its name is another runner's id
    assert rows["rn_D"][2] is not None
