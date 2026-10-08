"""The tenants revision creates the tenant record and the hub-level tables beside it, and carries
every existing row into one first tenant: a fresh ``ten_`` id named ``default``, created by ``migration``.

Seeded with literal SQL rather than importing ``src/blizzard/hub/store/schema.py``, whose tables keep
changing shape after this revision."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

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
