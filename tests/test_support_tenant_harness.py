"""The tenant test harness: a migrated store names its one tenant, a raw test insert carries it only
into a tenant-owned table, and a hub-scoped statement or engine carries the guard's exemption mark."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import HUB_SCOPED_OPTION
from tests.support import default_tenant, hub_level_connections, hub_scoped, hub_scoped_engine, stamp

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _head_store(tmp_path: Path) -> sa.Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return create_engine_from_url(db_url)


def _tenant_ids(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(sa.select(schema.tenants.c.tenant_id)).scalars())


def _add_tenant(engine: sa.Engine, tenant_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(schema.tenants).values(tenant_id=tenant_id, name="other", created_at=_T0, created_by="usr_1")
        )


def test_default_tenant_is_the_migrated_stores_only_tenant(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)

    tenant = default_tenant(engine)

    assert _tenant_ids(engine) == [tenant]
    assert tenant.startswith("ten_")


def test_default_tenant_is_read_once_per_engine(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)
    first = default_tenant(engine)
    _add_tenant(engine, "ten_second")

    assert default_tenant(engine) == first


def test_default_tenant_refuses_a_store_with_no_tenant(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)
    with engine.begin() as conn:
        conn.execute(sa.delete(schema.tenants))

    with pytest.raises(AssertionError, match="exactly one tenant; this one holds 0"):
        default_tenant(engine)


def test_default_tenant_refuses_a_store_with_several_tenants(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)
    _add_tenant(engine, "ten_second")

    with pytest.raises(AssertionError, match="exactly one tenant; this one holds 2"):
        default_tenant(engine)


def test_stamp_leaves_a_table_without_a_tenant_key_unchanged() -> None:
    assert "tenant_id" not in schema.chunk_pause_facts.c

    values = stamp(schema.chunk_pause_facts, chunk_id="ch_1", paused=True)

    assert values == {"chunk_id": "ch_1", "paused": True}


@pytest.mark.parametrize("table", ["tenants", "tenant_deletions"])
def test_stamp_leaves_a_global_table_unchanged_even_with_a_tenant_key(table: str) -> None:
    global_table = schema.metadata.tables[table]
    assert table in schema.GLOBAL_TABLES
    assert "tenant_id" in global_table.c

    assert stamp(global_table, tenant_id="ten_1", name="n") == {"tenant_id": "ten_1", "name": "n"}
    assert stamp(global_table, name="n") == {"name": "n"}


def test_stamp_carries_the_stores_tenant_into_a_tenant_owned_table(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)
    owned = sa.Table(
        "probe_rows",
        sa.MetaData(),
        sa.Column("probe_id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False),
    )
    owned.create(engine)

    with engine.begin() as conn:
        conn.execute(sa.insert(owned).values(stamp(owned, probe_id="p_1")))
        conn.execute(sa.insert(owned).values(stamp(owned, probe_id="p_2", tenant_id="ten_named")))
        rows = dict(conn.execute(sa.select(owned.c.probe_id, owned.c.tenant_id)).tuples().all())

    assert rows == {"p_1": default_tenant(engine), "p_2": "ten_named"}


def test_hub_scoped_marks_the_statement_and_leaves_the_original_unmarked() -> None:
    plain = sa.select(schema.tenants.c.tenant_id)

    marked = hub_scoped(plain)

    assert marked.get_execution_options()[HUB_SCOPED_OPTION] is True
    assert HUB_SCOPED_OPTION not in plain.get_execution_options()


def test_hub_scoped_engine_marks_every_statement_it_executes(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)
    seen: list[bool] = []

    @event.listens_for(engine, "before_execute")
    def _record(
        conn: sa.Connection, clause: object, multiparams: object, params: object, options: dict[str, object]
    ) -> None:
        seen.append(conn.get_execution_options().get(HUB_SCOPED_OPTION, False))

    scoped = hub_scoped_engine(engine)
    with scoped.connect() as conn:
        conn.execute(sa.select(schema.tenants.c.tenant_id))
    with engine.connect() as conn:
        conn.execute(sa.select(schema.tenants.c.tenant_id))

    assert scoped.get_execution_options()[HUB_SCOPED_OPTION] is True
    assert HUB_SCOPED_OPTION not in engine.get_execution_options()
    assert seen == [True, False]


def test_hub_level_connections_is_the_unscoped_seam(tmp_path: Path) -> None:
    engine = _head_store(tmp_path)

    connections = hub_level_connections(engine)

    assert type(connections) is HubStoreConnections
    with connections.read("count tenants") as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(schema.tenants)).scalar_one() == 1
