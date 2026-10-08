"""The tenant statement guard — a ``before_execute`` listener that refuses a statement reaching a tenant-owned table
without naming its tenant.

It walks every compiled ``Select``, ``Update`` and ``Delete``, nested ones included, and fails when a tenant-owned table
appears in a ``FROM`` without a ``tenant_id`` equality among the conjuncts of that statement's ``WHERE`` or a join's
``ON``; and every ``Insert`` into a tenant-owned table whose column list lacks ``tenant_id``. A statement carrying the
:data:`~blizzard.hub.tenancy.HUB_SCOPED_OPTION` execution option, on itself or on its connection or engine, is
deliberately cross-tenant and passes unwalked. Raw SQL (``text()``, ``exec_driver_sql``) is not walked.

The hub's guard watches only ``schema.metadata``'s tables: a migration's frozen ``sa.table()`` constructs and the runner
store's tables are other objects, so they pass. It is installed process-wide, and only when the process was started
with ``BLIZZARD_TENANT_GUARD=1`` — production leaves it off.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sqlalchemy import Engine, MetaData, Table, event
from sqlalchemy.engine import Connection
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, ClauseElement, ColumnClause, Grouping
from sqlalchemy.sql.selectable import (
    Alias,
    AliasedReturnsRows,
    CompoundSelect,
    FromClause,
    Join,
    Lateral,
    Select,
    TableClause,
)

from blizzard.hub.store import schema
from blizzard.hub.tenancy import HUB_SCOPED_OPTION

#: The environment variable that switches the hub's guard on, set to ``1``. A subprocess inherits it.
GUARD_ENVIRONMENT_VARIABLE = "BLIZZARD_TENANT_GUARD"

_TENANT_COLUMN = "tenant_id"

_Scope = frozenset[int]


class UnscopedTenantStatement(Exception):
    """Raised before a statement executes when it reaches a tenant-owned table without naming its tenant."""

    def __init__(self, violations: Sequence[str], statement: ClauseElement) -> None:
        super().__init__(f"{'; '.join(violations)} — in: {_rendered(statement)}")
        self.violations = tuple(violations)


def _is_tenant_owned(table: Table) -> bool:
    return _tenant_key(table) is not None and table.name not in schema.GLOBAL_TABLES


def _tenant_key(table: Table) -> str | None:
    """The key the ``tenant_id`` column is addressed by in values and parameters."""
    return next((column.key for column in table.columns if column.name == _TENANT_COLUMN), None)


def _named_insert_keys(insert: Insert, table: Table) -> frozenset[str]:
    """The column keys an insert names itself — ``values()`` in each of its forms, or ``from_select()``'s names.

    SQLAlchemy keeps these only on private attributes until it compiles the statement."""
    names: list[object] = [*(insert._values or {}), *(insert._select_names or ())]
    for row in insert._multi_values:
        for entry in row:
            names.extend(entry if isinstance(entry, Mapping) else table.columns[: len(entry)])
    return frozenset(name.key if isinstance(name, ColumnClause) else str(name) for name in names)


def _rendered(statement: ClauseElement) -> str:
    try:
        return str(statement)
    except Exception:  # a dialect-only construct the default compiler cannot render
        return type(statement).__name__


class TenantStatementGuard:
    """Checks statements against the tenant-owned tables of one ``MetaData``."""

    def __init__(self, metadata: MetaData) -> None:
        self._metadata = metadata

    def before_execute(
        self,
        conn: Connection,
        clauseelement: object,
        multiparams: Sequence[Mapping[str, Any]],
        params: Mapping[str, Any],
        execution_options: Mapping[str, Any],
    ) -> None:
        """The ``before_execute`` event's signature; ``execution_options`` already merges the statement's, the
        connection's and the engine's options."""
        if execution_options.get(HUB_SCOPED_OPTION):
            return
        self.check(clauseelement, parameter_keys=_parameter_keys(multiparams, params))

    def check(self, statement: object, *, parameter_keys: frozenset[str] = frozenset()) -> None:
        """Raise :class:`UnscopedTenantStatement` naming every unscoped use of a tenant-owned table in ``statement``.

        ``parameter_keys`` are the keys of the parameters it executes with, which add to an insert's column list."""
        if not isinstance(statement, Select | CompoundSelect | Insert | Update | Delete):
            return
        violations: list[str] = []
        self._walk(statement, frozenset(), frozenset(), parameter_keys, violations, set())
        if violations:
            raise UnscopedTenantStatement(sorted(set(violations)), statement)

    def _walk(
        self,
        element: ClauseElement,
        nested: _Scope,
        derived: _Scope,
        parameter_keys: frozenset[str],
        violations: list[str],
        seen: set[tuple[int, _Scope, _Scope]],
    ) -> None:
        """``nested`` holds the FROM elements a subquery in a ``WHERE``, a column or an ``EXISTS`` may correlate to —
        its enclosing statements' own; ``derived`` those a subquery in a ``FROM`` may, which excludes its sibling FROM
        elements unless it is ``LATERAL``. A correlated element is the enclosing statement's to scope."""
        key = (id(element), nested, derived)
        if key in seen or isinstance(element, TableClause | ColumnClause):
            return
        seen.add(key)
        if isinstance(element, Select):
            own = self._check_select(element, nested, violations)
            nested, derived = nested | own, nested
        elif isinstance(element, Update | Delete):
            own = self._check_dml_where(element, nested, violations)
            nested, derived = nested | own, nested
        elif isinstance(element, Insert):
            self._check_insert(element, parameter_keys, violations)
        elif isinstance(element, AliasedReturnsRows) and not isinstance(element, Lateral):
            nested = derived
        for child in element.get_children():
            self._walk(child, nested, derived, frozenset(), violations, seen)

    def _check_select(self, select: Select[Any], nested: _Scope, violations: list[str]) -> _Scope:
        froms = select.get_final_froms()
        leaves: list[FromClause] = []
        onclauses: list[ClauseElement] = []
        for from_ in froms:
            if id(from_) not in nested:
                _flatten(from_, leaves, onclauses)
        own = [leaf for leaf in leaves if id(leaf) not in nested]
        self._require_pins(own, [select.whereclause, *onclauses], "select", violations)
        return frozenset(id(leaf) for leaf in own) | frozenset(id(from_) for from_ in froms)

    def _check_dml_where(self, statement: Update | Delete, nested: _Scope, violations: list[str]) -> _Scope:
        leaves: list[FromClause] = []
        _flatten(statement.table, leaves, [])
        for from_ in _column_froms(statement):
            if id(from_) not in nested and all(from_ is not leaf for leaf in leaves):
                leaves.append(from_)
        verb = "update" if isinstance(statement, Update) else "delete"
        self._require_pins(leaves, [statement.whereclause], verb, violations)
        return frozenset(id(leaf) for leaf in leaves)

    def _check_insert(self, insert: Insert, parameter_keys: frozenset[str], violations: list[str]) -> None:
        table = insert.table
        if not (isinstance(table, Table) and self._guards(table)):
            return
        if _tenant_key(table) not in parameter_keys | _named_insert_keys(insert, table):
            violations.append(f"insert into tenant-owned table {table.name} without {_TENANT_COLUMN}")

    def _require_pins(
        self,
        leaves: Sequence[FromClause],
        conditions: Sequence[ClauseElement | None],
        verb: str,
        violations: list[str],
    ) -> None:
        conjuncts = [conjunct for condition in conditions for conjunct in _conjuncts(condition)]
        for leaf in leaves:
            table = _underlying_table(leaf)
            if table is None or not self._guards(table):
                continue
            if not any(_pins_tenant(conjunct, leaf) for conjunct in conjuncts):
                violations.append(
                    f"{verb} names tenant-owned table {_leaf_name(leaf, table)} "
                    f"without a {_TENANT_COLUMN} equality in its WHERE or join ON"
                )

    def _guards(self, table: Table) -> bool:
        return table.metadata is self._metadata and _is_tenant_owned(table)


def _parameter_keys(multiparams: Sequence[Mapping[str, Any]], params: Mapping[str, Any]) -> frozenset[str]:
    first = multiparams[0] if multiparams and isinstance(multiparams[0], Mapping) else {}
    return frozenset(str(key) for key in (*params, *first))


def _flatten(from_: FromClause, leaves: list[FromClause], onclauses: list[ClauseElement]) -> None:
    if isinstance(from_, Join):
        _flatten(from_.left, leaves, onclauses)
        _flatten(from_.right, leaves, onclauses)
        if from_.onclause is not None:
            onclauses.append(from_.onclause)
    else:
        leaves.append(from_)


def _column_froms(statement: Update | Delete) -> Iterator[FromClause]:
    """The FROM elements an ``UPDATE … FROM`` or ``DELETE … USING`` reaches through its own columns, nested
    statements excluded."""
    stack: list[ClauseElement] = list(statement.get_children())
    while stack:
        element = stack.pop()
        if isinstance(element, Select | AliasedReturnsRows):
            continue
        if isinstance(element, ColumnClause) and isinstance(element.table, FromClause):
            yield element.table
        stack.extend(element.get_children())


def _underlying_table(leaf: FromClause) -> Table | None:
    if isinstance(leaf, Table):
        return leaf
    if isinstance(leaf, Alias) and isinstance(leaf.element, Table):
        return leaf.element
    return None


def _leaf_name(leaf: FromClause, table: Table) -> str:
    return f"{table.name} (as {leaf.name})" if isinstance(leaf, Alias) else table.name


def _conjuncts(condition: ClauseElement | None) -> Iterator[ClauseElement]:
    if condition is None:
        return
    if isinstance(condition, Grouping):
        yield from _conjuncts(condition.element)
    elif isinstance(condition, BooleanClauseList) and condition.operator is operators.and_:
        for clause in condition.clauses:
            yield from _conjuncts(clause)
    else:
        yield condition


def _pins_tenant(conjunct: ClauseElement, leaf: FromClause) -> bool:
    if not isinstance(conjunct, BinaryExpression) or conjunct.operator is not operators.eq:
        return False
    pinned = [_is_tenant_column(side, leaf) for side in (conjunct.left, conjunct.right)]
    return any(pinned) and not all(pinned)


def _is_tenant_column(side: ClauseElement, leaf: FromClause) -> bool:
    return isinstance(side, ColumnClause) and side.table is leaf and side.name == _TENANT_COLUMN


_HUB_GUARD = TenantStatementGuard(schema.metadata)


def guard_hub_statement(
    conn: Connection,
    clauseelement: object,
    multiparams: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    execution_options: Mapping[str, Any],
) -> None:
    """The class-level ``Engine`` listener :func:`install_tenant_guard` registers: the hub's guard over
    ``schema.metadata``."""
    _HUB_GUARD.before_execute(conn, clauseelement, multiparams, params, execution_options)


def install_tenant_guard(environ: Mapping[str, str] = os.environ) -> bool:
    """Register :func:`guard_hub_statement` on every ``Engine`` in this process when ``environ`` switches the guard on,
    once however often it is called. Return whether the guard is installed."""
    if environ.get(GUARD_ENVIRONMENT_VARIABLE) != "1":
        return event.contains(Engine, "before_execute", guard_hub_statement)
    if not event.contains(Engine, "before_execute", guard_hub_statement):
        event.listen(Engine, "before_execute", guard_hub_statement)
    return True
