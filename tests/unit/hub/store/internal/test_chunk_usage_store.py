"""The chunk-usage store's own statement, ``usage_total_stmt`` (unit
tier): it compiles under both dialects, stays on the portable expression surface, and
selects only ungrouped aggregates — so it returns one row by construction, never a
per-fact object. Mirrors ``tests/unit/hub/store/internal/test_analytics_operational_store.py``'s sweep shape."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BinaryExpression, TextClause
from sqlalchemy.sql.functions import Function

from blizzard.hub.store.internal import chunk_usage_store as store_module
from blizzard.hub.store.scoped import ScopedStatements
from blizzard.hub.tenancy import StoreScope, TenantId

_SINCE = datetime(2026, 8, 12, tzinfo=UTC)
_UNTIL = datetime(2026, 8, 13, tzinfo=UTC)
_STMTS = ScopedStatements(StoreScope(TenantId("ten_usage")))


def test_usage_total_stmt_compiles_under_both_dialects() -> None:
    for until in (None, _UNTIL):
        stmt = store_module.usage_total_stmt(_STMTS, _SINCE, until)
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert str(stmt.compile(dialect=dialect))


def test_usage_total_stmt_leaves_no_raw_text_on_the_portable_surface() -> None:
    for until in (None, _UNTIL):
        stmt = store_module.usage_total_stmt(_STMTS, _SINCE, until)
        assert type(stmt).__module__.startswith("sqlalchemy.sql.")
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)]


def test_usage_total_stmt_selects_only_ungrouped_aggregates() -> None:
    """No ``GROUP BY`` and every selected column is a `func.*` aggregate — so the
    statement returns exactly one row regardless of how many facts match, and
    ``usage_total_since`` builds no per-row `UsageFact` object."""
    stmt = store_module.usage_total_stmt(_STMTS, _SINCE, None)
    assert "GROUP BY" not in str(stmt.compile())
    for column in stmt.selected_columns:
        element = column.element if isinstance(column, sa.Label) else column
        assert isinstance(element, Function | BinaryExpression), column
