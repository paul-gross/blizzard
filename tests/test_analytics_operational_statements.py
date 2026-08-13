"""The operational analytics query adapter's statements: every one it executes compiles
under both dialects and stays on the portable expression surface (blizzard#256, Phases
2-4 — unit tier). Mirrors ``test_analytics_event_query_statements.py``'s sweep shape."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import ClauseElement, visitors
from sqlalchemy.sql.elements import TextClause

from blizzard.hub.domain.analytics.operational import OperationalCriteria
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import analytics_operational_store as store_module

pytestmark = pytest.mark.unit

#: Every filter set at once, so no branch of the statement builder goes uncompiled.
_CRITERIA = OperationalCriteria(
    graph_id="gr_1", source="github", since=datetime(2026, 8, 12, tzinfo=UTC), until=datetime(2026, 8, 13, tzinfo=UTC)
)


def _executed_statements() -> dict[str, ClauseElement]:
    m = store_module
    return {
        "_lease_min_stmt": m._lease_min_stmt(),
        "_duration_lease_min_stmt": m._duration_lease_min_stmt(_CRITERIA),
        "_source_chunks_stmt": m._source_chunks_stmt("github"),
        "_duration_window_groups_stmt": m._duration_window_groups_stmt(_CRITERIA),
        "_duration_rows_stmt": m._duration_rows_stmt(_CRITERIA),
        "_spend_filtered_stmt": m._spend_filtered_stmt(select(s.usage_facts), _CRITERIA),
        "_spend_group_stmt": m._spend_group_stmt(_CRITERIA, group_col=s.usage_facts.c.node_id),
        "_spend_by_node_stmt": m._spend_by_node_stmt(_CRITERIA),
        "_spend_by_graph_stmt": m._spend_by_graph_stmt(_CRITERIA),
        "_spend_by_chunk_stmt": m._spend_by_chunk_stmt(_CRITERIA, cursor="ch_01J9Z3M0P8QK7V2S4W6X8Y0A1B", limit=200),
        "_judged_distribution_stmt": m._judged_distribution_stmt(_CRITERIA),
        "_candidate_lease_epochs_stmt": m._candidate_lease_epochs_stmt(_CRITERIA),
        "_candidate_chunk_ids_stmt": m._candidate_chunk_ids_stmt(_CRITERIA),
        "_chunk_max_lease_epoch_stmt": m._chunk_max_lease_epoch_stmt(_CRITERIA),
        "_chunk_transitions_stmt": m._chunk_transitions_stmt(_CRITERIA),
        "_chunk_migrations_stmt": m._chunk_migrations_stmt(_CRITERIA),
        "_chunk_bounces_stmt": m._chunk_bounces_stmt(_CRITERIA),
        "_chunks_graph_stmt": m._chunks_graph_stmt(_CRITERIA),
        "_graph_entry_nodes_stmt": m._graph_entry_nodes_stmt(["gr_1"]),
    }


def test_every_statement_the_store_executes_compiles_under_both_dialects() -> None:
    for name, stmt in _executed_statements().items():
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert str(stmt.compile(dialect=dialect)), name


@pytest.mark.parametrize("builder_name", ["_duration_rows_stmt", "_duration_lease_min_stmt"])
def test_duration_statements_narrow_via_a_correlated_subquery_not_a_bound_id_list(builder_name: str) -> None:
    """F1: a materialized chunk-id list bound one host parameter per element, hitting
    sqlite's variable ceiling at 32,767 chunks. Pinned structurally — both builders take
    ``criteria``, not an id list, narrowing via a row-value ``IN (SELECT ...)``."""
    stmt = getattr(store_module, builder_name)(_CRITERIA)
    sql = str(stmt.compile(dialect=sqlite.dialect()))
    assert "IN (SELECT" in sql


def test_no_statement_the_store_executes_leaves_the_portable_surface() -> None:
    assert store_module.__name__.endswith("analytics_operational_store")  # pins which adapter this sweep covers
    for name, stmt in _executed_statements().items():
        assert type(stmt).__module__.startswith("sqlalchemy.sql."), name
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)], name


def test_the_compile_sweep_reaches_every_statement_the_store_can_execute() -> None:
    """Every ``_stmt`` builder has a compile-test entry, even a shared piece never itself
    the direct ``.execute()`` argument, and every actual call site is built by one of
    them — no inline, unbuilt statement bypasses the sweep."""
    builders = {name for name in vars(store_module) if name.endswith("_stmt")}
    assert builders == set(_executed_statements())

    source = ast.parse(Path(store_module.__file__ or "").read_text())
    executed = [
        node.args[0]
        for node in ast.walk(source)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
    ]
    # 3 durations (window groups, group rows, lease min) + 3 spend + outcomes' own
    # 8-query fan-out — one `.execute()` site each.
    assert len(executed) == 14
    for arg in executed:
        built_by_a_builder = (
            isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id.endswith("_stmt")
        )
        assert built_by_a_builder, ast.unparse(arg)
