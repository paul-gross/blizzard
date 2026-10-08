"""The tenants revision creates the tenant record and the hub-level tables beside it, and carries
every existing row into one first tenant: a fresh ``ten_`` id named ``default``, created by ``migration``.
The tenant_id backfill then keys every table outside the global list and stamps each existing row with
that tenant.

Seeded with literal SQL, or with rows built from the store's reflected shape at the pinned revision,
rather than importing ``src/blizzard/hub/store/schema.py``, whose tables keep changing shape after
these revisions."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy import event

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationClockMissingError, MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations, migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store.schema import GLOBAL_TABLES

pytestmark = pytest.mark.component

_BEFORE = "20261008_1000_drop_work_item_proposals"
_TENANTS = "20261008_1100_tenants"
_BACKFILL = "20261008_1200_tenant_id_backfill"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ADDED = {"tenants", "tenant_deletions", "invitations", "invitation_facts", "hub_event_log"}

_SEED = (
    "INSERT INTO graphs (graph_id, name, entry_node_id, definition_yaml, created_at)"
    " VALUES ('gr_1', 'g', 'nd_1', '', :at)",
    "INSERT INTO chunks (chunk_id, graph_id, minted_at) VALUES ('ch_1', 'gr_1', :at)",
    "INSERT INTO chunks (chunk_id, graph_id, minted_at) VALUES ('ch_2', 'gr_1', :at)",
    "INSERT INTO users (id, username, display_name, email, role, created_at)"
    " VALUES ('usr_1', 'ada', 'Ada', 'ada@example.com', 'admin', :at)",
    "INSERT INTO event_log (recorded_at, severity, kind, chunk_id, message)"
    " VALUES (:at, 'info', 'chunk-minted', 'ch_1', 'minted')",
    "INSERT INTO event_log (recorded_at, severity, kind, message)"
    " VALUES (:at, 'warning', 'trace-export-failed', 'export failed')",
)


def _store(tmp_path: Path) -> tuple[str, sa.Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    return db_url, create_engine_from_url(db_url)


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _row_counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: conn.execute(sa.select(sa.func.count()).select_from(sa.table(table))).scalar_one()
            for table in _tables(engine)
        }


def _tenant_rows(engine: sa.Engine) -> list[sa.Row]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text("SELECT tenant_id, name, created_at, created_by FROM tenants")).all())


def _schema(engine: sa.Engine) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        table: (
            frozenset((c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
            frozenset(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)),
            frozenset(
                (tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in inspector.get_foreign_keys(table)
            ),
        )
        for table in _tables(engine)
    }


def _assert_the_first_tenant(engine: sa.Engine) -> None:
    [(tenant_id, name, created_at, created_by)] = _tenant_rows(engine)
    parsed = Id.parse(tenant_id)
    assert parsed is not None and parsed.has_prefix(IdPrefix.TENANT)
    assert parsed.minted_at is not None
    assert (name, created_by) == ("default", "migration")
    assert created_at is not None


def test_a_fresh_store_starts_with_the_one_default_tenant(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        _assert_the_first_tenant(engine)
    finally:
        engine.dispose()


def test_the_first_tenant_is_stamped_by_the_clock_the_migration_was_given(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    at = datetime(2026, 3, 4, 5, 6, 7, 890000, tzinfo=UTC)
    try:
        Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(at)).runner.upgrade(_TENANTS)

        with engine.connect() as conn:
            tenant_id, created_at = conn.execute(
                sa.select(sa.column("tenant_id"), sa.column("created_at", UtcDateTime)).select_from(sa.table("tenants"))
            ).one()
        assert created_at == at
        parsed = Id.parse(tenant_id)
        assert parsed is not None and parsed.minted_at == at
    finally:
        engine.dispose()


def test_a_hub_upgrade_through_the_tenants_revision_without_a_clock_fails_naming_the_fix(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=db_url)
    try:
        runner.upgrade(_BEFORE)

        with pytest.raises(MigrationClockMissingError, match="migrate through MigrationRunner with a clock"):
            runner.upgrade(_TENANTS)
        assert runner.current_revision() == _BEFORE
    finally:
        engine.dispose()


def test_a_carried_over_store_keeps_every_row_and_gains_one_tenant(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(sa.text(statement), {"at": _T0})
        before = _row_counts(engine)

        runner.upgrade(_TENANTS)

        after = _row_counts(engine)
        assert {table: after[table] for table in before} == before
        assert {table: after[table] for table in _ADDED} == {
            "tenants": 1,
            "tenant_deletions": 0,
            "invitations": 0,
            "invitation_facts": 0,
            "hub_event_log": 0,
        }
        _assert_the_first_tenant(engine)
    finally:
        engine.dispose()


def test_downgrade_removes_exactly_what_the_revision_added(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(sa.text(statement), {"at": _T0})
        schema_before, counts_before = _schema(engine), _row_counts(engine)

        runner.upgrade(_TENANTS)
        assert _tables(engine) - set(schema_before) == _ADDED

        runner.downgrade(_BEFORE)
        assert _schema(engine) == schema_before
        assert _row_counts(engine) == counts_before
    finally:
        engine.dispose()


def test_a_rerun_after_downgrade_mints_a_fresh_single_tenant(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_TENANTS)
        [(first, *_)] = _tenant_rows(engine)
        runner.downgrade(_BEFORE)
        runner.upgrade(_TENANTS)
        [(second, *_)] = _tenant_rows(engine)
        assert first != second
        _assert_the_first_tenant(engine)
    finally:
        engine.dispose()


def test_upgrade_creates_only_what_is_missing_and_never_a_second_tenant(tmp_path: Path) -> None:
    """Re-entry: a store already holding the tenant record (an interrupted earlier run) is completed,
    not duplicated."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE tenants (tenant_id VARCHAR NOT NULL PRIMARY KEY, name VARCHAR NOT NULL,"
                    " created_at DATETIME NOT NULL, created_by VARCHAR NOT NULL)"
                )
            )
            conn.execute(
                sa.text("INSERT INTO tenants VALUES ('ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B', 'default', :at, 'migration')"),
                {"at": _T0},
            )

        runner.upgrade(_TENANTS)

        assert _tables(engine) >= _ADDED
        assert [row[0] for row in _tenant_rows(engine)] == ["ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B"]
    finally:
        engine.dispose()


def test_the_tenant_tables_take_their_declared_shape(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    try:
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_TENANTS)
        inspector = sa.inspect(engine)

        def columns(table: str) -> dict[str, bool]:
            return {str(c["name"]): bool(c["nullable"]) for c in inspector.get_columns(table)}

        def foreign_keys(table: str) -> dict[str | None, tuple[tuple[str, ...], str]]:
            return {
                fk["name"]: (tuple(fk["constrained_columns"]), fk["referred_table"])
                for fk in inspector.get_foreign_keys(table)
            }

        def indexes(table: str) -> dict[str | None, tuple[str | None, ...]]:
            return {i["name"]: tuple(i["column_names"]) for i in inspector.get_indexes(table)}

        assert columns("tenants") == {"tenant_id": False, "name": False, "created_at": False, "created_by": False}
        assert inspector.get_pk_constraint("tenants")["constrained_columns"] == ["tenant_id"]
        assert not inspector.get_unique_constraints("tenants")
        assert not [i for i in inspector.get_indexes("tenants") if i["unique"]]

        assert columns("tenant_deletions") == {
            "id": False,
            "tenant_id": False,
            "name": False,
            "deleted_at": True,
            "deleted_by": False,
            "rows_removed": True,
        }
        assert not foreign_keys("tenant_deletions")
        assert indexes("tenant_deletions") == {"ix_tenant_deletions_tenant_id": ("tenant_id",)}

        assert columns("invitations") == {
            "invitation_id": False,
            "tenant_id": False,
            "email": False,
            "role": False,
            "token_hash": False,
            "created_by": False,
            "created_at": False,
            "expires_at": False,
        }
        assert foreign_keys("invitations") == {"fk_invitations_tenant_id": (("tenant_id",), "tenants")}
        assert indexes("invitations") == {
            "ix_invitations_tenant_id": ("tenant_id",),
            "ix_invitations_token_hash": ("token_hash",),
        }

        assert columns("invitation_facts") == {
            "id": False,
            "tenant_id": False,
            "invitation_id": False,
            "kind": False,
            "actor_user_id": False,
            "recorded_at": False,
        }
        assert foreign_keys("invitation_facts") == {
            "fk_invitation_facts_tenant_id": (("tenant_id",), "tenants"),
            "fk_invitation_facts_invitation_id": (("invitation_id",), "invitations"),
        }
        assert {u["name"] for u in inspector.get_unique_constraints("invitation_facts")} == {
            "uq_invitation_facts_invitation_id"
        }
        assert {c["name"] for c in inspector.get_check_constraints("invitation_facts")} == {"ck_invitation_facts_kind"}
        assert indexes("invitation_facts") == {"ix_invitation_facts_tenant_id": ("tenant_id",)}

        assert columns("hub_event_log") == {
            "id": False,
            "recorded_at": False,
            "severity": False,
            "kind": False,
            "message": False,
            "detail": True,
        }
        assert indexes("hub_event_log") == {"ix_hub_event_log_recorded_at": ("recorded_at",)}
    finally:
        engine.dispose()


def test_the_global_list_names_exactly_the_tables_that_carry_no_tenant_key() -> None:
    assert (
        frozenset(
            {
                "tenants",
                "tenant_deletions",
                "users",
                "identities",
                "sessions",
                "auth_state",
                "auth_facts",
                "superuser_bootstrap",
                "hub_admin_facts",
                "config_import_facts",
                "membership_facts",
                "hub_event_log",
                "trace_cursor",
                "egress_cursor",
            }
        )
        == GLOBAL_TABLES
    )


_ALLOWED_VALUES = re.compile(r"^\s*(\w+)\s+IN\s*\(\s*'([^']*)'")


def _seed_every_tenant_owned_table(engine: sa.Engine) -> set[str]:
    """One row in every table outside the global list, built from the store's reflected shape: every
    column without a server default gets a value of its own type, or the first value its ``IN (...)``
    CHECK constraint allows; filling every column satisfies the "this column implies that one" checks.
    Foreign keys stay unenforced on the store's connections, so no parent row is needed."""
    reflected = sa.MetaData()
    reflected.reflect(engine)
    inspector = sa.inspect(engine)
    seeded: set[str] = set()
    with engine.begin() as conn:
        for table in reflected.sorted_tables:
            if table.name in GLOBAL_TABLES or table.name == "alembic_version":
                continue
            allowed = {
                match.group(1): match.group(2)
                for check in inspector.get_check_constraints(table.name)
                if (match := _ALLOWED_VALUES.match(check["sqltext"])) is not None
            }
            row = {
                column.name: allowed.get(column.name, _value_for(column))
                for column in table.columns
                if column.server_default is None
            }
            conn.execute(sa.insert(table).values(row))
            seeded.add(table.name)
    return seeded


def _value_for(column: sa.Column) -> object:
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return f"{column.name}-1"
    if python_type is bool:
        return False
    if python_type is int:
        return 1
    if python_type is float:
        return 1.0
    if python_type is datetime:
        return _T0
    if python_type is bytes:
        return b"1"
    return f"{column.name}-1"


def _keyed_tables(engine: sa.Engine) -> dict[str, bool]:
    """Every table outside the global list, and whether it carries ``tenant_id``."""
    inspector = sa.inspect(engine)
    return {
        table: any(column["name"] == "tenant_id" for column in inspector.get_columns(table))
        for table in _tables(engine) - GLOBAL_TABLES
    }


def _tenants_of(engine: sa.Engine, table: str) -> list[str | None]:
    with engine.connect() as conn:
        return list(conn.execute(sa.select(sa.column("tenant_id")).select_from(sa.table(table))).scalars())


def test_a_carried_over_store_reaches_head_with_every_row_in_the_first_tenant(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        seeded = _seed_every_tenant_owned_table(engine)
        before = _row_counts(engine)

        runner.upgrade("head")

        [(tenant_id, *_)] = _tenant_rows(engine)
        assert _keyed_tables(engine) == dict.fromkeys(_tables(engine) - GLOBAL_TABLES, True)
        assert seeded == set(before) - GLOBAL_TABLES
        assert {table: _row_counts(engine)[table] for table in seeded} == {table: before[table] for table in seeded}
        assert {table: set(_tenants_of(engine, table)) for table in seeded} == {table: {tenant_id} for table in seeded}
    finally:
        engine.dispose()


def test_a_rerun_of_the_backfill_over_a_partly_keyed_store_finishes(tmp_path: Path) -> None:
    """Re-entry: a run that stopped after keying some tables, and stamping some of their rows, is
    completed — no column added twice, every row still null stamped."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(sa.text(statement), {"at": _T0})
        runner.upgrade(_TENANTS)
        [(tenant_id, *_)] = _tenant_rows(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE graphs ADD COLUMN tenant_id VARCHAR"))
            conn.execute(sa.text("ALTER TABLE chunks ADD COLUMN tenant_id VARCHAR"))
            conn.execute(
                sa.text("UPDATE chunks SET tenant_id = :tenant WHERE chunk_id = 'ch_1'"), {"tenant": tenant_id}
            )

        runner.upgrade(_BACKFILL)

        assert all(_keyed_tables(engine).values())
        assert [c["name"] for c in sa.inspect(engine).get_columns("chunks")].count("tenant_id") == 1
        for table in ("graphs", "chunks", "event_log"):
            assert set(_tenants_of(engine, table)) == {tenant_id}, table
    finally:
        engine.dispose()


def _backfill_batch_rows() -> int:
    """The backfill's batch size, read from the revision as Alembic loads it."""
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_BACKFILL)
    assert revision is not None
    return revision.module._BATCH_ROWS


def _seed_past_a_batch(engine: sa.Engine) -> int:
    """Fill ``event_log`` (an integer key) and ``graph_artifacts`` (a two-column text key, its rows spread over
    several graphs so a page boundary falls inside one graph) with more rows than two of the backfill's batches hold,
    leaving a part-filled last page. Returns the row count seeded into each."""
    count = 2 * _backfill_batch_rows() + 7
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO event_log (recorded_at, severity, kind, message) VALUES (:at, 'info', 'k', 'm')"),
            [{"at": _T0}] * count,
        )
        conn.execute(
            sa.text("INSERT INTO graph_artifacts (graph_id, name, ordinal, content) VALUES (:graph, :name, 1, '')"),
            [{"graph": f"gr_{n % 3}", "name": f"artifact-{n:05}"} for n in range(count)],
        )
    return count


def _tenant_counts(engine: sa.Engine, table: str) -> dict[str | None, int]:
    tenant = sa.column("tenant_id", sa.String)
    with engine.connect() as conn:
        rows = conn.execute(sa.select(tenant, sa.func.count()).select_from(sa.table(table)).group_by(tenant)).tuples()
        return dict(rows.all())


@contextmanager
def _rows_each_update_changed() -> Iterator[list[tuple[str, int]]]:
    """Every ``UPDATE`` any engine in the process runs while the context is open — the migration runner's engine
    included — as the table it names and the rows it changed, in issue order."""
    updates: list[tuple[str, int]] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        words = statement.split()
        if words[0].upper() == "UPDATE":
            updates.append((words[1], cursor.rowcount))

    event.listen(sa.Engine, "after_cursor_execute", record)
    try:
        yield updates
    finally:
        event.remove(sa.Engine, "after_cursor_execute", record)


def test_the_backfill_stamps_every_row_of_a_table_holding_more_than_one_batch(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_TENANTS)
        count = _seed_past_a_batch(engine)
        [(tenant_id, *_)] = _tenant_rows(engine)

        with _rows_each_update_changed() as updates:
            runner.upgrade(_BACKFILL)

        for table in ("event_log", "graph_artifacts"):
            assert _tenant_counts(engine, table) == {tenant_id: count}, table
            stamped = [rows for updated, rows in updates if updated == table]
            assert len(stamped) >= 3, table
            assert max(stamped) <= _backfill_batch_rows(), table
            assert sum(stamped) == count, table
    finally:
        engine.dispose()


def test_a_rerun_of_the_backfill_over_rows_stamped_across_batches_stamps_the_rest(tmp_path: Path) -> None:
    """Re-entry inside a table: the rows a stopped run already stamped are scattered through the key range and span
    whole pages, and the rerun stamps every row between and after them."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_TENANTS)
        count = _seed_past_a_batch(engine)
        batch = _backfill_batch_rows()
        [(tenant_id, *_)] = _tenant_rows(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE event_log ADD COLUMN tenant_id VARCHAR"))
            conn.execute(sa.text("ALTER TABLE graph_artifacts ADD COLUMN tenant_id VARCHAR"))
            conn.execute(
                sa.text("UPDATE event_log SET tenant_id = :tenant WHERE id % 3 = 0 OR id BETWEEN :low AND :high"),
                {"tenant": tenant_id, "low": batch // 2, "high": batch + batch // 2},
            )
            conn.execute(
                sa.text("UPDATE graph_artifacts SET tenant_id = :tenant WHERE graph_id = 'gr_1'"), {"tenant": tenant_id}
            )
        assert all(None in _tenant_counts(engine, table) for table in ("event_log", "graph_artifacts"))

        runner.upgrade(_BACKFILL)

        for table in ("event_log", "graph_artifacts"):
            assert _tenant_counts(engine, table) == {tenant_id: count}, table
    finally:
        engine.dispose()


def test_the_backfill_refuses_a_store_holding_two_tenants_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_TENANTS)
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO tenants VALUES ('ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B', 'second', :at, 'migration')"),
                {"at": _T0},
            )

        with pytest.raises(RuntimeError, match="needs exactly one"):
            runner.upgrade(_BACKFILL)

        assert runner.current_revision() == _TENANTS
        assert {table for table, keyed in _keyed_tables(engine).items() if keyed} == {"invitations", "invitation_facts"}
    finally:
        engine.dispose()


def test_the_backfill_downgrade_removes_exactly_the_column_it_added(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        _seed_every_tenant_owned_table(engine)
        runner.upgrade(_TENANTS)
        schema_before, counts_before = _schema(engine), _row_counts(engine)

        runner.upgrade(_BACKFILL)
        runner.downgrade(_TENANTS)

        assert _schema(engine) == schema_before
        assert _row_counts(engine) == counts_before
    finally:
        engine.dispose()
