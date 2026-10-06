"""Runner-store migrations: the store keys nothing on the runner's id and keeps one identity row."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.store import schema as runner_schema
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors

pytestmark = pytest.mark.component

_IDENTITY_PARENT = "20261005_1000_runner_credential_renewal_facts"
_KEYED_TABLES = ("hub_control", "local_pause_facts", "daemon_liveness", "leases")

_OLD = datetime(2026, 9, 1, 12, tzinfo=UTC)
_NEW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_LATER = datetime(2026, 10, 6, 12, tzinfo=UTC)

# Each keyed table's shape at the identity revision's parent — only the columns these tests write.
_keyed = sa.MetaData()
_hub_control = sa.Table(
    "hub_control",
    _keyed,
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("paused", sa.Boolean()),
    sa.Column("updated_at", UtcDateTime()),
)
_daemon_liveness = sa.Table(
    "daemon_liveness",
    _keyed,
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("alive_at", UtcDateTime()),
)
_local_pause_facts = sa.Table(
    "local_pause_facts",
    _keyed,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("runner_id", sa.String()),
    sa.Column("paused", sa.Boolean()),
    sa.Column("set_at", UtcDateTime()),
    sa.Column("set_by", sa.String()),
    sa.Column("reason", sa.String()),
)
_leases = sa.Table(
    "leases",
    _keyed,
    sa.Column("lease_id", sa.String(), primary_key=True),
    sa.Column("chunk_id", sa.String()),
    sa.Column("epoch", sa.Integer()),
    sa.Column("runner_id", sa.String()),
    sa.Column("created_at", UtcDateTime()),
)


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = runner_runtime.init_environment(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_IDENTITY_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _seed_a_store_spanning_a_rename(engine: sa.Engine) -> None:
    """``runner-local`` (paused at the hub) renamed to ``r-claude`` (running): two ids in one store."""
    with engine.begin() as conn:
        conn.execute(
            _hub_control.insert(),
            [
                {"runner_id": "runner-local", "paused": True, "updated_at": _OLD},
                {"runner_id": "r-claude", "paused": False, "updated_at": _NEW},
            ],
        )
        conn.execute(
            _daemon_liveness.insert(),
            [{"runner_id": "runner-local", "alive_at": _OLD}, {"runner_id": "r-claude", "alive_at": _NEW}],
        )
        conn.execute(
            _local_pause_facts.insert(),
            [
                {"runner_id": "runner-local", "paused": True, "set_at": _OLD, "set_by": "operator"},
                {"runner_id": "r-claude", "paused": False, "set_at": _NEW, "set_by": "operator"},
            ],
        )
        conn.execute(
            _leases.insert(),
            [
                {"lease_id": "l_old", "chunk_id": "ch_1", "epoch": 1, "runner_id": "runner-local", "created_at": _OLD},
                {"lease_id": "l_new", "chunk_id": "ch_2", "epoch": 1, "runner_id": "r-claude", "created_at": _NEW},
            ],
        )


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _rows(engine: sa.Engine, statement: str) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(sa.text(statement))]


def test_identity_migration_keeps_the_newest_liveness_ids_singletons_and_every_history_row(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_store_spanning_a_rename(engine)
        migration.upgrade("head")

        for table in _KEYED_TABLES:
            assert "runner_id" not in _columns(engine, table)
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        assert _rows(engine, "SELECT count(*) FROM hub_control") == [(1,)]
        assert store.hub_paused() is False  # r-claude's mirror, not the older id's pause
        assert store.hub_contact_at() == _NEW
        assert _rows(engine, "SELECT count(*) FROM daemon_liveness") == [(1,)]
        assert store.last_daemon_liveness() == _NEW
        assert _rows(engine, "SELECT paused FROM local_pause_facts ORDER BY id") == [(1,), (0,)]
        assert _rows(engine, "SELECT lease_id FROM leases ORDER BY lease_id") == [("l_new",), ("l_old",)]
        assert store.runner_identity() is None
    finally:
        engine.dispose()


_CEILING = "spend ceiling: $50.00 over 24h"


@pytest.mark.parametrize(
    ("facts", "paused", "reason"),
    [
        ([("runner-local", True, _NEW, "usage limit: claude_code")], False, None),
        ([("r-claude", True, _OLD, _CEILING), ("runner-local", False, _NEW, None)], True, _CEILING),
    ],
    ids=["former-id-paused-last", "former-id-cleared-last"],
)
def test_identity_migration_keeps_the_kept_ids_local_brake_when_a_former_id_wrote_the_newest_fact(
    tmp_path: Path, facts: list[tuple[str, bool, datetime, str | None]], paused: bool, reason: str | None
) -> None:
    """Each id read only its own facts before; after, the brake is the newest fact whichever id wrote it."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        with engine.begin() as conn:
            conn.execute(
                _daemon_liveness.insert(),
                [{"runner_id": "runner-local", "alive_at": _OLD}, {"runner_id": "r-claude", "alive_at": _NEW}],
            )
            conn.execute(
                _local_pause_facts.insert(),
                [{"runner_id": i, "paused": p, "set_at": at, "set_by": "runner", "reason": r} for i, p, at, r in facts],
            )
        migration.upgrade("head")

        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        assert (store.local_paused(), store.local_pause_reason()) == (paused, reason)
        assert _rows(engine, "SELECT count(*) FROM local_pause_facts") == [(len(facts) + 1,)]
    finally:
        engine.dispose()


def test_identity_migration_downgrades_onto_the_identity_rows_name_and_upgrades_back(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_store_spanning_a_rename(engine)
        migration.upgrade("head")
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        store.record_runner_identity(RunnerIdentity("rn_01JX0000000000000000000000", "r-claude", _LATER))
        store.set_hub_paused(paused=True, at=_LATER)

        migration.downgrade(_IDENTITY_PARENT)
        assert "runner_identity" not in sa.inspect(engine).get_table_names()
        assert _rows(engine, "SELECT runner_id, paused FROM hub_control") == [("r-claude", 1)]
        assert _rows(engine, "SELECT runner_id FROM daemon_liveness") == [("r-claude",)]
        assert _rows(engine, "SELECT DISTINCT runner_id FROM local_pause_facts") == [("r-claude",)]
        assert _rows(engine, "SELECT DISTINCT runner_id FROM leases") == [("r-claude",)]
        assert not next(c for c in sa.inspect(engine).get_columns("leases") if c["name"] == "runner_id")["nullable"]

        migration.upgrade("head")
        assert store.hub_paused() is True
        assert store.last_daemon_liveness() == _NEW
        assert _rows(engine, "SELECT count(*) FROM local_pause_facts") == [(2,)]
        assert store.runner_identity() is None
    finally:
        engine.dispose()


def test_identity_migration_downgrades_a_never_registered_store_onto_the_pre_change_default_id(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade("head")
        SqlAlchemyRunnerStore(engine, runner_store_errors()).set_hub_paused(paused=True, at=_LATER)
        migration.downgrade(_IDENTITY_PARENT)
        assert _rows(engine, "SELECT runner_id, paused FROM hub_control") == [("runner-local", 1)]
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", _KEYED_TABLES)
def test_frozen_keyed_tables_carry_the_runner_id_until_the_identity_revision(table: str, tmp_path: Path) -> None:
    """``bzh:frozen-revisions``: migrated forward from ``base``, never down from head, so a freeze
    that wrongly absorbed the drop shows."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    engine = create_engine_from_url(url)
    try:
        runner.upgrade(_IDENTITY_PARENT)
        assert "runner_id" in _columns(engine, table)
        assert "runner_identity" not in sa.inspect(engine).get_table_names()
        runner.upgrade("head")
        assert "runner_id" not in _columns(engine, table)
        assert "runner_identity" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_rebuilt_store_holds_no_identity_until_a_registration_records_the_hubs(tmp_path: Path) -> None:
    """A store keeps only the identity a registration answered, so a rebuilt one cannot fork a new
    one: it holds none until the hub answers the runner's token again."""
    identity = RunnerIdentity("rn_01JX0000000000000000000000", "r-claude", _NEW)
    first = runner_runtime.init_environment(tmp_path / "first")
    first_engine = create_engine_from_url(first.db_url)
    rebuilt = runner_runtime.init_environment(tmp_path / "rebuilt")
    rebuilt_engine = create_engine_from_url(rebuilt.db_url)
    try:
        store = SqlAlchemyRunnerStore(first_engine, runner_store_errors())
        store.record_runner_identity(identity)
        renamed = RunnerIdentity(identity.runner_id, "r-claude-2", _LATER)
        store.record_runner_identity(renamed)
        assert store.runner_identity() == renamed
        assert _rows(first_engine, "SELECT count(*) FROM runner_identity") == [(1,)]

        again = SqlAlchemyRunnerStore(rebuilt_engine, runner_store_errors())
        assert again.runner_identity() is None
        again.record_runner_identity(identity)
        assert again.runner_identity() == identity
    finally:
        first_engine.dispose()
        rebuilt_engine.dispose()


def test_release_floor_backfill_does_not_include_a_later_equal_instant_mint(tmp_path: Path) -> None:
    config = runner_runtime.init_environment(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade("20261003_1000_runner_transcript_segment_spawn_cwd")
    engine = create_engine_from_url(config.db_url)
    instant = datetime(2026, 7, 13, 12, tzinfo=UTC)
    try:
        with engine.begin() as conn:
            for lease_id, epoch in (("old", 1), ("new", 3)):
                conn.execute(
                    _leases.insert().values(
                        lease_id=lease_id, chunk_id="ch_1", epoch=epoch, runner_id="r1", created_at=instant
                    )
                )
            conn.execute(
                runner_schema.lease_closures.insert().values(
                    lease_id="old", chunk_id="ch_1", node_id="nd_build", reason="released", closed_at=instant
                )
            )
            conn.execute(
                runner_schema.binding_releases.insert().values(
                    chunk_id="ch_1", environment_id="e1", released_at=instant
                )
            )
        migration.upgrade("head")
        with engine.connect() as conn:
            floor = conn.execute(sa.select(runner_schema.binding_releases.c.lease_epoch_floor)).scalar_one()
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        assert floor == 1
        assert store.has_lease_in_binding_tenure("ch_1", instant)
    finally:
        engine.dispose()
