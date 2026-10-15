"""The identity row records its tenant: beside the runner's id it keeps the tenant the hub answered
with, for display. A store written before tenants gains the two columns empty and fills them at the
runner's first contact after the upgrade, with no configuration change and nothing else touched; no
other table carries a tenant."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.hub.identity import IdentityState, IdentityVerdictHolder, RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.lifecycle.identity_check import IdentityCheck
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.store import schema as runner_schema
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
    migrated_store_at,
    runner_store_errors,
    serving,
)

pytestmark = pytest.mark.component

_PARENT = "20261006_1000_runner_identity"
_REVISION = "20261008_1000_runner_identity_tenant"
_TENANT_COLUMNS = {"tenant_id", "tenant_name"}

_RUNNER = "rn_01JX0000000000000000000000"
_REGISTERED = datetime(2026, 10, 6, 12, tzinfo=UTC)
_NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)

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
    config = runner_runtime.init_environment(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _seed_a_pre_tenant_identity(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            _identity_at_parent.insert().values(runner_id=_RUNNER, runner_name="r-claude", registered_at=_REGISTERED)
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
            _leases_at_parent.insert().values(lease_id="lease_1", chunk_id="ch_1", epoch=1, created_at=_REGISTERED)
        )
        conn.execute(
            _lease_context_at_parent.insert().values(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                retries_max=2,
                recorded_at=_REGISTERED,
            )
        )


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _ctx(store: SqlAlchemyRunnerStore, hub: FakeHub, *, checked: bool = False) -> LoopContext:
    """A freshly started runner over ``store``: its holder seeded from the row as composition seeds it
    at boot, its verdict unchecked — or, ``checked``, already matched and its hub ungated."""
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="r-claude", runner_id=_RUNNER, bindings=serving(1)),
        registered=False,
        identity_verdict=None if checked else IdentityVerdictHolder("blizzard runner store reset /runner"),
    )
    return dataclasses.replace(ctx, identity=RunnerIdentityHolder(store.runner_identity()))


def _tenant_hub(tenant_id: str | None = "ten_default", tenant_name: str | None = "default") -> FakeHub:
    hub = FakeHub(default_runner_id=_RUNNER)
    hub.tenant_id, hub.tenant_name = tenant_id, tenant_name
    return hub


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
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        assert store.runner_identity() == RunnerIdentity(_RUNNER, "r-claude", _REGISTERED)
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
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED, "ten_default", "default"))

        migration.downgrade(_PARENT)
        assert not _TENANT_COLUMNS & _columns(engine, "runner_identity")
        with engine.connect() as conn:
            assert [tuple(r) for r in conn.execute(sa.text("SELECT runner_id, runner_name FROM runner_identity"))] == [
                (_RUNNER, "r-claude")
            ]

        migration.upgrade(_REVISION)
        assert store.runner_identity() == RunnerIdentity(_RUNNER, "r-claude", _REGISTERED)
    finally:
        engine.dispose()


def test_no_table_but_the_identity_rows_carries_a_tenant(tmp_path: Path) -> None:
    """One store, one tenant: nothing keys or filters on the tenant, in the declared schema or in a store migrated to
    head — only the runner's and the host's identity rows name it."""
    declared = {t.name for t in runner_schema.metadata.sorted_tables if any("tenant" in c.name for c in t.columns)}
    assert declared == {"runner_identity", "host_identity"}

    url = f"sqlite:///{tmp_path / 'store.db'}"
    engine = create_engine_from_url(url)
    try:
        migrated_store_at(url)
        inspector = sa.inspect(engine)
        migrated = {
            t for t in inspector.get_table_names() if any("tenant" in c["name"] for c in inspector.get_columns(t))
        }
        assert migrated == {"runner_identity", "host_identity"}
        assert not any(
            "tenant" in column
            for table in inspector.get_table_names()
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
            if column is not None
        )
    finally:
        engine.dispose()


def test_a_runner_upgraded_from_before_tenants_adopts_its_tenant_at_its_first_contact(tmp_path: Path) -> None:
    """No configuration change and no stranded chunk: the same token, the same runner, the same lease."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_pre_tenant_identity(engine)
        _seed_a_pre_tenant_lease(engine)
        migration.upgrade("head")
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        ctx = _ctx(store, _tenant_hub())

        IdentityCheck(ctx).run()

        adopted = RunnerIdentity(_RUNNER, "r-claude", _REGISTERED, "ten_default", "default")
        assert ctx.identity_verdict.verdict().state is IdentityState.MATCHED
        assert store.runner_identity() == adopted
        assert ctx.identity.current() == adopted
        assert store.lease("lease_1") is not None
    finally:
        engine.dispose()


def test_an_empty_row_takes_the_registered_runner_with_its_tenant(tmp_path: Path) -> None:
    """The check writes nothing into an empty row; the registration after it records the configured runner and its
    tenant."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    ctx = _ctx(store, _tenant_hub())

    IdentityCheck(ctx).run()
    assert store.runner_identity() is None

    Registration(ctx).run()
    recorded = store.runner_identity()
    assert recorded is not None
    assert (recorded.runner_id, recorded.tenant_id, recorded.tenant_name) == (_RUNNER, "ten_default", "default")


def test_a_mismatched_answer_records_nothing_of_the_other_runners_tenant(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED))
    hub = _tenant_hub("ten_other", "other")
    hub.issued[hub.token] = "rn_other"
    ctx = _ctx(store, hub)

    IdentityCheck(ctx).run()

    assert ctx.identity_verdict.verdict().state is IdentityState.MISMATCHED
    assert store.runner_identity() == RunnerIdentity(_RUNNER, "r-claude", _REGISTERED)


@pytest.mark.parametrize(
    ("answered", "kept"),
    [
        (("ten_default", "default"), ("ten_default", "default")),
        (("ten_default", "Platform team"), ("ten_default", "Platform team")),
        ((None, None), ("ten_default", "default")),
    ],
    ids=["same-tenant", "renamed-tenant", "hub-names-none"],
)
def test_each_registration_keeps_the_tenant_its_reply_names_else_the_rows(
    tmp_path: Path, answered: tuple[str | None, str | None], kept: tuple[str | None, str | None]
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED, "ten_default", "default"))
    ctx = _ctx(store, _tenant_hub(*answered), checked=True)

    Registration(ctx).run()

    recorded = store.runner_identity()
    assert recorded is not None and (recorded.tenant_id, recorded.tenant_name) == kept
    assert ctx.identity.current() == recorded


def test_a_first_registration_after_the_upgrade_fills_the_tenant_when_the_check_could_not(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_RUNNER)
    store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _REGISTERED))

    Registration(_ctx(store, _tenant_hub(), checked=True)).run()

    assert store.runner_identity() == RunnerIdentity(_RUNNER, "r-claude", _NOW, "ten_default", "default")
