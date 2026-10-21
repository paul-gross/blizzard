"""The operational analytics query adapter's statements: every one it executes compiles
under both dialects and stays on the portable expression surface (unit tier).
Mirrors ``tests/test_analytics_event_query_statements.py``'s sweep shape."""

from __future__ import annotations

import ast
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql import ClauseElement, visitors
from sqlalchemy.sql.elements import TextClause

from blizzard.hub.domain.kernel.pagination import encode_cursor
from blizzard.hub.domain.observability.analytics.operational import OperationalCriteria
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import analytics_operational_store as store_module
from blizzard.hub.store.scoped import ScopedStatements
from blizzard.hub.tenancy import StoreScope, TenantId

pytestmark = pytest.mark.unit

_STMTS = ScopedStatements(StoreScope(TenantId("ten_analytics")))

#: Every filter set at once, so no branch of the statement builder goes uncompiled.
_CRITERIA = OperationalCriteria(
    graph_id="gr_1",
    source="github",
    since=datetime(2026, 8, 12, tzinfo=UTC),
    until=datetime(2026, 8, 13, tzinfo=UTC),
    model="m-a",
    harness_id="claude_code",
    harness_version="1.0",
)


def _executed_statements() -> dict[str, ClauseElement]:
    m = store_module
    return {
        "_lease_min_stmt": m._lease_min_stmt(
            _STMTS,
        ),
        "_duration_lease_min_stmt": m._duration_lease_min_stmt(_STMTS, _CRITERIA),
        "_source_chunks_stmt": m._source_chunks_stmt(_STMTS, "github"),
        "_duration_window_groups_stmt": m._duration_window_groups_stmt(_STMTS, _CRITERIA),
        "_duration_rows_stmt": m._duration_rows_stmt(_STMTS, _CRITERIA),
        "_spend_filtered_stmt": m._spend_filtered_stmt(_STMTS, _CRITERIA, *s.usage_facts.c),
        "_spend_group_stmt": m._spend_group_stmt(_STMTS, _CRITERIA, group_col=s.usage_facts.c.node_id),
        "_spend_by_node_stmt": m._spend_by_node_stmt(_STMTS, _CRITERIA),
        "_spend_by_graph_stmt": m._spend_by_graph_stmt(_STMTS, _CRITERIA),
        "_spend_by_model_stmt": m._spend_by_model_stmt(_STMTS, _CRITERIA),
        "_spend_by_chunk_stmt": m._spend_by_chunk_stmt(
            _STMTS, _CRITERIA, cursor=encode_cursor("ch_01J9Z3M0P8QK7V2S4W6X8Y0A1B"), limit=200
        ),
        "_judged_distribution_stmt": m._judged_distribution_stmt(_STMTS, _CRITERIA),
        "_candidate_lease_epochs_stmt": m._candidate_lease_epochs_stmt(_STMTS, _CRITERIA),
        "_candidate_chunk_ids_stmt": m._candidate_chunk_ids_stmt(_STMTS, _CRITERIA),
        "_chunk_max_lease_epoch_stmt": m._chunk_max_lease_epoch_stmt(_STMTS, _CRITERIA),
        "_chunk_transitions_stmt": m._chunk_transitions_stmt(_STMTS, _CRITERIA),
        "_chunk_migrations_stmt": m._chunk_migrations_stmt(_STMTS, _CRITERIA),
        "_chunk_bounces_stmt": m._chunk_bounces_stmt(_STMTS, _CRITERIA),
        "_chunks_graph_stmt": m._chunks_graph_stmt(_STMTS, _CRITERIA),
        "_candidate_graph_ids_stmt": m._candidate_graph_ids_stmt(_STMTS, _CRITERIA),
        "_graph_entry_nodes_stmt": m._graph_entry_nodes_stmt(_STMTS, _CRITERIA),
    }


def test_every_statement_the_store_executes_compiles_under_both_dialects() -> None:
    for name, stmt in _executed_statements().items():
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert str(stmt.compile(dialect=dialect)), name


@pytest.mark.parametrize("builder_name", ["_duration_rows_stmt", "_duration_lease_min_stmt"])
def test_duration_statements_narrow_via_a_correlated_subquery_not_a_bound_id_list(builder_name: str) -> None:
    """Pinned structurally — a row-value ``IN (SELECT ...)`` keyed on the whole
    ``(chunk_id, epoch)`` pair, not ``chunk_id`` alone (which still matches a bare
    ``"IN (SELECT"`` check but leaks a migrated chunk's other-graph epoch)."""
    stmt = getattr(store_module, builder_name)(_STMTS, _CRITERIA)
    sql = str(stmt.compile(dialect=sqlite.dialect()))
    assert re.search(r"\(\w+\.chunk_id, \w+\.epoch\) IN \(SELECT", sql), sql


def test_graph_entry_nodes_narrows_via_a_correlated_subquery_not_a_bound_id_list() -> None:
    """``graphs.graph_id`` is a per-mint id, so a materialized list bound one host
    parameter per graph version any chunk has ever run — pinned structurally, the same
    correlated-subquery shape used above."""
    sql = str(store_module._graph_entry_nodes_stmt(_STMTS, _CRITERIA).compile(dialect=sqlite.dialect()))
    assert "graph_id IN (SELECT" in sql, sql


def test_no_statement_the_store_executes_leaves_the_portable_surface() -> None:
    assert store_module.__name__.endswith("analytics_operational_store")  # pins which adapter this sweep covers
    for name, stmt in _executed_statements().items():
        assert type(stmt).__module__.startswith("sqlalchemy.sql."), name
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)], name


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="reads store_module.__file__'s own source; under mutmut that file also carries every "
    "generated mutant body, which this sweep would wrongly hold to the same pattern",
)
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
    # 2 durations (rows, lease min — review round 4: no separate group-existence
    # probe) + 4 spend + outcomes' own 8-query fan-out — one `.execute()` site each.
    assert len(executed) == 14
    for arg in executed:
        built_by_a_builder = (
            isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id.endswith("_stmt")
        )
        assert built_by_a_builder, ast.unparse(arg)


#: Statements whose rows are narrowed by a lease attempt's usage facts, not by a usage fact directly.
_ATTEMPT_NARROWED = [
    "_duration_window_groups_stmt",
    "_judged_distribution_stmt",
    "_candidate_lease_epochs_stmt",
]

#: The statements the failure half must leave whole, because the failed node is inferred from earlier movement.
_LEFT_WHOLE = ["_chunk_transitions_stmt", "_chunk_migrations_stmt", "_chunk_bounces_stmt"]


@pytest.mark.parametrize("builder_name", _ATTEMPT_NARROWED)
def test_an_attempt_statement_carries_a_tenant_scoped_exists_over_usage_facts(builder_name: str) -> None:
    sql = str(getattr(store_module, builder_name)(_STMTS, _CRITERIA).compile(dialect=sqlite.dialect()))
    exists = sql[sql.index("EXISTS") :]
    assert "usage_facts.chunk_id =" in exists and "usage_facts.epoch =" in exists, sql
    assert "usage_facts.tenant_id" in exists, sql
    for column in ("model", "harness_id", "harness_version"):
        assert f"usage_facts.{column} =" in exists, (column, sql)


@pytest.mark.parametrize("builder_name", _ATTEMPT_NARROWED)
def test_no_provenance_leaves_an_attempt_statement_without_the_exists(builder_name: str) -> None:
    sql = str(getattr(store_module, builder_name)(_STMTS, OperationalCriteria()).compile(dialect=sqlite.dialect()))
    assert "EXISTS" not in sql, sql


@pytest.mark.parametrize("builder_name", _LEFT_WHOLE)
def test_a_provenance_filter_never_narrows_the_failure_halfs_whole_history_fetches(builder_name: str) -> None:
    """Their chunk narrowing arrives only through the candidate lease epochs subquery."""
    sql = str(getattr(store_module, builder_name)(_STMTS, _CRITERIA).compile(dialect=sqlite.dialect()))
    assert sql.count("usage_facts.model") == 1 and "usage_facts.epoch" in sql, sql


def test_spend_filters_the_usage_facts_directly_not_by_exists() -> None:
    sql = str(store_module._spend_by_model_stmt(_STMTS, _CRITERIA).compile(dialect=sqlite.dialect()))
    assert "EXISTS" not in sql and "usage_facts.model = " in sql and "GROUP BY usage_facts.model" in sql, sql
