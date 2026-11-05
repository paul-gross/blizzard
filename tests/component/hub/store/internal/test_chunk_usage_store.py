"""The chunk-usage store's spend read against a real store: the range predicate plans through
the tenant's index. Migrates a real disk-backed sqlite file and reads the query planner."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store.internal import chunk_usage_store as store_module
from blizzard.hub.store.scoped import ScopedStatements
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support.scaffolding import migrated_hub_at, seed_chunk, seed_graph

_SINCE = datetime(2026, 8, 12, tzinfo=UTC)
_STMTS = ScopedStatements(StoreScope(TenantId("ten_usage")))


def test_spend_range_query_plans_as_an_index_search(tmp_path: Path) -> None:
    """The spend read's range predicate plans through the tenant's
    ``ix_usage_facts_tenant_recorded_at_id`` rather than a full table scan."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "g1", at=_SINCE)
        seed_chunk(conn, "ch_1", graph_id="g1", at=_SINCE)
    compiled = str(
        store_module.usage_total_stmt(_STMTS, _SINCE, None).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    assert any("ix_usage_facts_tenant_recorded_at_id" in str(row) for row in plan), plan
