"""The analytics query adapter's statements: every one it executes compiles under both
dialects and stays on the portable expression surface (blizzard#255, Phase 2 — unit
tier)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import ClauseElement, visitors
from sqlalchemy.sql.elements import TextClause

from blizzard.hub.domain.analytics.queries import EventQueryCriteria
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import analytics_event_query_store as store_module

pytestmark = pytest.mark.unit

#: Every filter set at once, so no branch of the statement builders goes uncompiled.
_CRITERIA = EventQueryCriteria(
    extractor_version="blizzard-analytics/2",
    kind="file_read",
    tool="Read",
    path_prefix="src/",
    node_id="nd_build",
    graph_id="gr_1",
    source="github",
    since=datetime(2026, 8, 12, tzinfo=UTC),
    until=datetime(2026, 8, 13, tzinfo=UTC),
)


def _executed_statements() -> dict[str, ClauseElement]:
    m = store_module
    return {
        "_filtered_stmt": m._filtered_stmt(select(s.transcript_events), _CRITERIA),
        "_events_stmt": m._events_stmt(_CRITERIA, cursor="7", limit=200),
        "_counts_stmt": m._counts_stmt(_CRITERIA, group_col=s.transcript_events.c.subject, kind="file_read"),
    }


def test_every_statement_the_store_executes_compiles_under_both_dialects() -> None:
    for name, stmt in _executed_statements().items():
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert "transcript_events" in str(stmt.compile(dialect=dialect)), name


def test_no_statement_the_store_executes_leaves_the_portable_surface() -> None:
    assert store_module.__name__.endswith("analytics_event_query_store")  # pins which adapter this sweep covers
    for name, stmt in _executed_statements().items():
        assert type(stmt).__module__.startswith("sqlalchemy.sql."), name
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)], name


def test_a_prefix_filter_compiles_to_a_dialect_independent_comparison() -> None:
    """A prefix match is `substr(...) = ...` under both dialects — LIKE's case folding
    differs between them, so an events page or a count would follow the backend."""
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        sql = str(_executed_statements()["_filtered_stmt"].compile(dialect=dialect))
        assert "substr" in sql.lower()
        assert "like" not in sql.lower()


def test_the_compile_sweep_reaches_every_statement_the_store_can_execute() -> None:
    builders = {name for name in vars(store_module) if name.endswith("_stmt")}
    assert builders == set(_executed_statements())

    source = ast.parse(Path(store_module.__file__ or "").read_text())
    executed = [
        node.args[0]
        for node in ast.walk(source)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
    ]
    assert executed
    for arg in executed:
        built_by_a_builder = isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in builders
        assert built_by_a_builder, ast.unparse(arg)
