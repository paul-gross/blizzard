"""Tests proving the ``20261008_1000_runner_identity_tenant`` revision."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.lifecycle.identity_check import IdentityCheck
from tests.config_inputs import init_host
from tests.migration_support_runner_identity_tenant import REGISTERED, RUNNER, started_context, tenant_hub
from tests.runner_fakes import (
    SqlAlchemyRunnerStore,
    runner_store_errors,
)

pytestmark = [pytest.mark.component]

_PARENT = "20261006_1000_runner_identity"

_REVISION = "20261008_1000_runner_identity_tenant"

_TENANT_COLUMNS = {"tenant_id", "tenant_name"}

# ``runner_identity`` at the revision's parent — the columns a pre-tenant runner wrote.
_identity_at_parent = sa.Table(
    "runner_identity",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("runner_id", sa.String()),
    sa.Column("runner_name", sa.String()),
    sa.Column("registered_at", UtcDateTime()),
)


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _seed_a_pre_tenant_identity(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            _identity_at_parent.insert().values(runner_id=RUNNER, runner_name="r-claude", registered_at=REGISTERED)
        )


# ``leases`` and ``lease_context`` at the revision's parent — the columns a pre-tenant runner's lease mint wrote.
_leases_at_parent = sa.table(
    "leases", sa.column("lease_id"), sa.column("chunk_id"), sa.column("epoch"), sa.column("created_at", UtcDateTime())
)

_lease_context_at_parent = sa.table(
    "lease_context",
    sa.column("lease_id"),
    sa.column("chunk_id"),
    sa.column("graph_id"),
    sa.column("node_id"),
    sa.column("node_name"),
    sa.column("retries_max"),
    sa.column("recorded_at", UtcDateTime()),
)


def _seed_a_pre_tenant_lease(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            _leases_at_parent.insert().values(lease_id="lease_1", chunk_id="ch_1", epoch=1, created_at=REGISTERED)
        )
        conn.execute(
            _lease_context_at_parent.insert().values(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                retries_max=2,
                recorded_at=REGISTERED,
            )
        )


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_the_upgrade_adds_both_columns_empty_and_keeps_the_row(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_pre_tenant_identity(engine)
        assert not _TENANT_COLUMNS & _columns(engine, "runner_identity")

        migration.upgrade(_REVISION)

        assert _columns(engine, "runner_identity") >= _TENANT_COLUMNS
        assert all(
            c["nullable"] for c in sa.inspect(engine).get_columns("runner_identity") if c["name"] in _TENANT_COLUMNS
        )
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), RUNNER)
        assert store.runner_identity() == RunnerIdentity(RUNNER, "r-claude", REGISTERED)
    finally:
        engine.dispose()


def test_the_upgrade_skips_a_column_already_present(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE runner_identity ADD COLUMN tenant_id VARCHAR"))
        migration.upgrade(_REVISION)
        assert _columns(engine, "runner_identity") >= _TENANT_COLUMNS
    finally:
        engine.dispose()


def test_the_downgrade_drops_both_columns_keeping_the_row_and_upgrades_back(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), RUNNER)
        store.record_runner_identity(RunnerIdentity(RUNNER, "r-claude", REGISTERED, "ten_default", "default"))

        migration.downgrade(_PARENT)
        assert not _TENANT_COLUMNS & _columns(engine, "runner_identity")
        with engine.connect() as conn:
            assert [tuple(r) for r in conn.execute(sa.text("SELECT runner_id, runner_name FROM runner_identity"))] == [
                (RUNNER, "r-claude")
            ]

        migration.upgrade(_REVISION)
        assert store.runner_identity() == RunnerIdentity(RUNNER, "r-claude", REGISTERED)
    finally:
        engine.dispose()


def test_a_runner_upgraded_from_before_tenants_adopts_its_tenant_at_its_first_contact(tmp_path: Path) -> None:
    """No configuration change and no stranded chunk: the same token, the same runner, the same lease."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_pre_tenant_identity(engine)
        _seed_a_pre_tenant_lease(engine)
        migration.upgrade("head")
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), RUNNER)
        ctx = started_context(store, tenant_hub())

        IdentityCheck(ctx).run()

        adopted = RunnerIdentity(RUNNER, "r-claude", REGISTERED, "ten_default", "default")
        assert ctx.identity_verdict.verdict().state is IdentityState.MATCHED
        assert store.runner_identity() == adopted
        assert ctx.identity.current() == adopted
        assert store.lease("lease_1") is not None
    finally:
        engine.dispose()
