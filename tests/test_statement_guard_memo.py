"""The statement guard's per-shape verdict cache (unit tier).

A verdict is remembered under the statement's SQLAlchemy cache key with its parameter keys, so bound values share one
and an insert's column names do not; a remembered violation refuses every later execution, for the tenant guard and the
runner guard alike; a statement without a cache key is walked every time; the cache is bounded."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.sql.elements import ClauseElement

from blizzard.foundation.store import statement_guard
from blizzard.foundation.store.statement_guard import KeyedStatementGuard, _Walk
from blizzard.hub.store import schema as hub_schema
from blizzard.hub.store.tenant_guard import TenantStatementGuard, UnscopedTenantStatement
from blizzard.runner.store import schema as runner_schema
from blizzard.runner.store.runner_guard import RUNNER_GUARD, UnscopedRunnerStatement

pytestmark = pytest.mark.unit

_hub_guard = TenantStatementGuard(hub_schema.metadata)
_hub_table = next(table for table in hub_schema.metadata.sorted_tables if _hub_guard.guards(table))
_hub_key = "tenant_id"
_leases = runner_schema.leases

_GUARDS: dict[str, tuple[KeyedStatementGuard, Any, str, type[Exception]]] = {
    "tenant": (_hub_guard, _hub_table, _hub_key, UnscopedTenantStatement),
    "runner": (RUNNER_GUARD, _leases, "runner_id", UnscopedRunnerStatement),
}


@pytest.fixture
def walks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    count: list[int] = []
    visit = _Walk.visit

    def counting(self: _Walk, element: ClauseElement, *args: Any, **kwargs: Any) -> None:
        if not self._seen:
            count.append(1)
        visit(self, element, *args, **kwargs)

    monkeypatch.setattr(statement_guard._Walk, "visit", counting)
    return count


@pytest.fixture(params=list(_GUARDS))
def guard_case(request: pytest.FixtureRequest) -> tuple[KeyedStatementGuard, Any, str, type[Exception]]:
    guard, table, key, error = _GUARDS[request.param]
    return (
        KeyedStatementGuard(
            guard._metadata, guard.scoping, exempt_option=guard._exempt_option, unscoped=guard._unscoped
        ),
        table,
        key,
        error,
    )


def test_an_unscoped_shape_is_refused_every_time_it_executes(
    guard_case: tuple[KeyedStatementGuard, Any, str, type[Exception]], walks: list[int]
) -> None:
    guard, table, _key, error = guard_case
    statement = sa.select(table)
    messages = []
    for _ in range(2):
        with pytest.raises(error) as raised:
            guard.check(statement)
        messages.append(str(raised.value))
    assert messages[0] == messages[1]
    assert len(walks) == 1


def test_statements_differing_only_in_bound_values_share_one_verdict(
    guard_case: tuple[KeyedStatementGuard, Any, str, type[Exception]], walks: list[int]
) -> None:
    guard, table, key, _error = guard_case
    first = sa.select(table).where(table.c[key] == "a")
    second = sa.select(table).where(table.c[key] == "b")
    guard.check(first)
    guard.check(second)
    guard.check(first)
    assert len(walks) == 1


def test_inserts_differing_in_parameter_keys_do_not_share_a_verdict(
    guard_case: tuple[KeyedStatementGuard, Any, str, type[Exception]], walks: list[int]
) -> None:
    guard, table, key, error = guard_case
    statement = sa.insert(table)
    guard.check(statement, parameter_keys=frozenset({key}))
    with pytest.raises(error):
        guard.check(statement, parameter_keys=frozenset())
    guard.check(statement, parameter_keys=frozenset({key}))
    assert len(walks) == 2


def test_a_statement_without_a_cache_key_is_walked_every_time(
    guard_case: tuple[KeyedStatementGuard, Any, str, type[Exception]],
    walks: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard, table, key, _error = guard_case
    statement = sa.select(table).where(table.c[key] == "a")
    monkeypatch.setattr(type(statement), "_generate_cache_key", lambda self: None)
    guard.check(statement)
    guard.check(statement)
    assert len(walks) == 2


def test_the_verdict_cache_is_bounded(
    guard_case: tuple[KeyedStatementGuard, Any, str, type[Exception]], monkeypatch: pytest.MonkeyPatch
) -> None:
    guard, table, key, _error = guard_case
    monkeypatch.setattr(statement_guard, "_VERDICT_CACHE_SIZE", 3)
    for index in range(10):
        guard.check(sa.select(table.c[key], sa.literal_column(f"c{index}")).where(table.c[key] == "a"))
    assert len(guard._verdicts) == 3
