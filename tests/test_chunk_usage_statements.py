"""The chunk-usage store's own statement, ``_usage_total_stmt`` (blizzard#517, unit
tier): it compiles under both dialects, stays on the portable expression surface, and
selects only ungrouped aggregates — so it returns one row by construction, never a
per-fact object. Mirrors ``test_analytics_operational_statements.py``'s sweep shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BinaryExpression, TextClause
from sqlalchemy.sql.functions import Function

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal import chunk_usage_store as store_module

from .support import seed_chunk, seed_graph

pytestmark = pytest.mark.unit

_SINCE = datetime(2026, 8, 12, tzinfo=UTC)
_UNTIL = datetime(2026, 8, 13, tzinfo=UTC)


def test_usage_total_stmt_compiles_under_both_dialects() -> None:
    for until in (None, _UNTIL):
        stmt = store_module._usage_total_stmt(_SINCE, until)
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert str(stmt.compile(dialect=dialect))


def test_usage_total_stmt_leaves_no_raw_text_on_the_portable_surface() -> None:
    for until in (None, _UNTIL):
        stmt = store_module._usage_total_stmt(_SINCE, until)
        assert type(stmt).__module__.startswith("sqlalchemy.sql.")
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)]


def test_usage_total_stmt_selects_only_ungrouped_aggregates() -> None:
    """No ``GROUP BY`` and every selected column is a `func.*` aggregate — so the
    statement returns exactly one row regardless of how many facts match, and
    ``usage_total_since`` builds no per-row `UsageFact` object (blizzard#517)."""
    stmt = store_module._usage_total_stmt(_SINCE, None)
    assert not stmt._group_by_clauses  # type: ignore[attr-defined]
    for column in stmt.selected_columns:
        element = column.element if isinstance(column, sa.Label) else column
        assert isinstance(element, Function | BinaryExpression), column


def test_spend_range_query_plans_as_an_index_search(tmp_path: Path) -> None:
    """The spend read's range predicate (blizzard#517) plans through
    ``ix_usage_facts_recorded_at`` rather than a full table scan."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "g1", at=_SINCE)
        seed_chunk(conn, "ch_1", graph_id="g1", at=_SINCE)
    compiled = str(
        store_module._usage_total_stmt(_SINCE, None).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    assert any("ix_usage_facts_recorded_at" in str(row) for row in plan), plan
