"""The statement guard — a ``before_execute`` listener that refuses a statement reaching a keyed table without naming
its key, for one :class:`~blizzard.foundation.store.key_scoping.KeyScoping` over one ``MetaData``.

It walks every compiled ``Select``, ``Update`` and ``Delete``, nested ones included, and fails when a keyed table is
rendered in a ``FROM`` without being scoped; and every ``Insert`` into a keyed table whose column list lacks the key. A
table is scoped by an equality among the conjuncts of a condition that filters its rows — the statement's ``WHERE``, an
inner join's ``ON``, or the ``ON`` of the left outer join it is the right side of — that equates its key to a bound
value, to the key of a keyed table it correlates to, or to the key of another table in the statement that is scoped. A
statement carrying the guard's exemption option, on itself or on its connection or engine, is deliberately unscoped
and passes unwalked. Raw SQL (``text()``, ``exec_driver_sql``) is not walked.

A guard watches only its own ``MetaData``'s tables: a migration's frozen ``sa.table()`` constructs and another store's
tables are other objects, so they pass."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Connection
from sqlalchemy.sql import operators
from sqlalchemy.sql.base import _from_objects
from sqlalchemy.sql.dml import Delete, DeleteDMLState, Insert, Update, UpdateDMLState
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    Cast,
    ClauseElement,
    ColumnClause,
    Grouping,
    TypeCoerce,
)
from sqlalchemy.sql.selectable import (
    CTE,
    Alias,
    AliasedReturnsRows,
    CompoundSelect,
    FromClause,
    Join,
    Lateral,
    Select,
    TableClause,
)

from blizzard.foundation.roles import adapter_model
from blizzard.foundation.store.key_scoping import KeyScoping, UnscopedKeyedStatement


def _column_key(table: Table, column_name: str) -> str | None:
    """The key the ``column_name`` column is addressed by in values and parameters."""
    return next((column.key for column in table.columns if column.name == column_name), None)


def _named_insert_keys(insert: Insert, table: Table) -> frozenset[str]:
    """The column keys an insert names itself — ``values()`` in each of its forms, or ``from_select()``'s names.

    SQLAlchemy keeps these only on private attributes until it compiles the statement."""
    names: list[object] = [*(insert._values or {}), *(insert._select_names or ())]
    for row in insert._multi_values:
        for entry in row:
            names.extend(entry if isinstance(entry, Mapping) else table.columns[: len(entry)])
    return frozenset(name.key if isinstance(name, ColumnClause) else str(name) for name in names)


@adapter_model
@dataclass(frozen=True)
class _Enclosing:
    """What a nested statement may correlate to, as SQLAlchemy's compiler tracks it: ``correlate`` holds the FROM
    elements of every enclosing statement, ``siblings`` those of the one directly enclosing it."""

    correlate: frozenset[FromClause] = frozenset()
    siblings: frozenset[FromClause] = frozenset()


_TOP = _Enclosing()


@dataclass(frozen=True)
class _Condition:
    """A ``WHERE`` or a join's ``ON``, with the leaves whose rows it filters: a ``WHERE`` and an inner join's ``ON``
    filter every leaf they span, a left outer join's ``ON`` only its right side, a full join's ``ON`` none."""

    clause: ClauseElement | None
    filters: frozenset[FromClause]


class KeyedStatementGuard:
    """Checks statements against the keyed tables of one ``MetaData`` under one :class:`KeyScoping`.

    It decides which tables each statement renders in its ``FROM`` the way SQLAlchemy's compiler does: a subquery in a
    ``WHERE``, a column or an ``EXISTS`` auto-correlates to the enclosing FROM elements only when it names more than
    one FROM element, and never under ``correlate(None)``; a subquery in a ``FROM`` is not auto-correlated to its
    siblings unless it is ``LATERAL``; a CTE is rendered at the top-level ``WITH`` and correlates to nothing. A
    correlated table is the enclosing statement's to scope."""

    def __init__(
        self,
        metadata: MetaData,
        scoping: KeyScoping,
        *,
        exempt_option: str,
        unscoped: Callable[[Sequence[str], ClauseElement], Exception] = UnscopedKeyedStatement,
    ) -> None:
        """``exempt_option`` is the execution option that marks a statement deliberately unscoped, which passes
        unwalked; ``unscoped`` builds the error :meth:`check` raises from the violations and the statement."""
        self._metadata = metadata
        self.scoping = scoping
        self._exempt_option = exempt_option
        self._unscoped = unscoped

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
        if execution_options.get(self._exempt_option):
            return
        self.check(clauseelement, parameter_keys=_parameter_keys(multiparams, params))

    def check(self, statement: object, *, parameter_keys: frozenset[str] = frozenset()) -> None:
        """Raise the guard's ``unscoped`` error naming every unscoped use of a keyed table in ``statement``.

        ``parameter_keys`` are the keys of the parameters it executes with, which add to an insert's column list."""
        if not isinstance(statement, Select | CompoundSelect | Insert | Update | Delete):
            return
        walk = _Walk(self, parameter_keys)
        walk.visit(statement, _TOP)
        if walk.violations:
            raise self._unscoped(sorted(set(walk.violations)), statement)

    def guards(self, leaf: FromClause) -> bool:
        """Whether ``leaf`` is a keyed table of this guard's ``MetaData``, or an alias (of an alias…) of it."""
        table = _underlying_table(leaf)
        return table is not None and table.metadata is self._metadata and self.scoping.is_keyed(table)


class _Walk:
    """One statement's walk, mirroring the compiler's descent: each nested statement sees the FROM elements it may
    correlate to, and each ``SELECT``, ``UPDATE`` and ``DELETE`` must scope every keyed table it renders."""

    def __init__(self, guard: KeyedStatementGuard, parameter_keys: frozenset[str]) -> None:
        self._guard = guard
        self._column = guard.scoping.key_column
        self._owner = guard.scoping.owner
        self._parameter_keys = parameter_keys
        self._seen: set[tuple[int, _Enclosing, bool, bool]] = set()
        self.violations: list[str] = []

    def visit(
        self, element: ClauseElement, enclosing: _Enclosing, *, asfrom: bool = False, lateral: bool = False
    ) -> None:
        """Walk ``element``; ``asfrom`` when it is rendered in a ``FROM``, ``lateral`` when that FROM is ``LATERAL``."""
        key = (id(element), enclosing, asfrom, lateral)
        if key in self._seen or isinstance(element, TableClause | ColumnClause):
            return
        self._seen.add(key)
        if isinstance(element, Select):
            self._visit_select(element, enclosing, asfrom=asfrom, lateral=lateral)
        elif isinstance(element, CompoundSelect):
            for arm in element.selects:
                self.visit(arm, enclosing, asfrom=asfrom, lateral=lateral)
            self._visit_expressions(element, enclosing, skip=frozenset(map(id, element.selects)))
        elif isinstance(element, Update | Delete):
            self._visit_dml(element)
        elif isinstance(element, Insert):
            self._check_insert(element)
            self._visit_expressions(element, _TOP)
        elif isinstance(element, CTE):
            self.visit(element.element, _TOP, asfrom=True)
        elif isinstance(element, Join):
            self.visit(element.left, enclosing, asfrom=True)
            self.visit(element.right, enclosing, asfrom=True)
            if element.onclause is not None:
                self.visit(element.onclause, enclosing)
        elif isinstance(element, AliasedReturnsRows):
            self.visit(element.element, enclosing, asfrom=True, lateral=lateral or isinstance(element, Lateral))
        else:
            for child in element.get_children():
                self.visit(child, enclosing)

    def _visit_select(self, select: Select[Any], enclosing: _Enclosing, *, asfrom: bool, lateral: bool) -> None:
        state = select._compile_state_factory(select, select._default_compiler())
        if asfrom and not lateral:
            froms = state._get_display_froms([*enclosing.correlate - enclosing.siblings], [])
        else:
            froms = state._get_display_froms([*enclosing.correlate], [*enclosing.siblings])
        own = frozenset(_from_objects(*froms))
        inner = _Enclosing(enclosing.correlate | own, own)
        self._require_pins(froms, [], select.whereclause, enclosing, "select")
        for from_ in froms:
            self.visit(from_, inner, asfrom=True)
        self._visit_expressions(select, inner)

    def _visit_dml(self, statement: Update | Delete) -> None:
        state = statement._compile_state_factory(statement, statement._default_compiler())
        assert isinstance(state, UpdateDMLState | DeleteDMLState)
        extra: Sequence[FromClause] = state._extra_froms
        main = [statement.table]
        own = frozenset(_from_objects(*main, *extra))
        inner = _Enclosing(own, own)
        verb = "update" if isinstance(statement, Update) else "delete"
        self._require_pins(main, extra, statement.whereclause, _TOP, verb)
        for from_ in extra:
            self.visit(from_, inner, asfrom=True)
        self._visit_expressions(statement, inner)

    def _visit_expressions(
        self, element: ClauseElement, enclosing: _Enclosing, *, skip: frozenset[int] = frozenset()
    ) -> None:
        """Walk ``element``'s children other than the FROM elements its FROM already rendered — a CTE excepted, which
        renders at the top-level ``WITH`` wherever it is attached."""
        for child in element.get_children():
            if id(child) in skip or (isinstance(child, FromClause) and not isinstance(child, CTE)):
                continue
            self.visit(child, enclosing)

    def _check_insert(self, insert: Insert) -> None:
        table = insert.table
        if not (isinstance(table, Table) and self._guard.guards(table)):
            return
        if _column_key(table, self._column) not in self._parameter_keys | _named_insert_keys(insert, table):
            self.violations.append(f"insert into {self._owner} table {table.name} without {self._column}")

    def _require_pins(
        self,
        froms: Sequence[FromClause],
        extra: Sequence[FromClause],
        where: ClauseElement | None,
        enclosing: _Enclosing,
        verb: str,
    ) -> None:
        """Require every keyed leaf of ``froms`` and ``extra`` to be scoped: equated, by a condition that filters it,
        to a value, to a keyed table it correlates to, or to another scoped leaf's key."""
        leaves: list[FromClause] = []
        conditions: list[_Condition] = []
        for from_ in [*froms, *extra]:
            leaves.extend(_flatten(from_, conditions))
        conditions.append(_Condition(where, frozenset(leaves)))
        guarded = [leaf for leaf in leaves if self._guard.guards(leaf)]
        if not guarded:
            return
        anchors = frozenset(from_ for from_ in enclosing.correlate if self._guard.guards(from_))
        scoped: set[FromClause] = set()
        edges: list[tuple[FromClause, FromClause]] = []
        for condition in conditions:
            for left, right in _equalities(condition.clause):
                for this, other in ((left, right), (right, left)):
                    leaf = _key_leaf(this, leaves, self._column)
                    if leaf is None or leaf not in condition.filters:
                        continue
                    other_leaf = _key_leaf(other, leaves, self._column)
                    if other_leaf is not None:
                        if other_leaf is not leaf:
                            edges.append((leaf, other_leaf))
                    elif _is_value(other) or _key_leaf(other, anchors, self._column) is not None:
                        scoped.add(leaf)
        grew = True
        while grew:
            reached = {leaf for leaf, other in edges if other in scoped} - scoped
            scoped |= reached
            grew = bool(reached)
        for leaf in guarded:
            if leaf not in scoped:
                table = _underlying_table(leaf)
                assert table is not None
                self.violations.append(
                    f"{verb} names {self._owner} table {_leaf_name(leaf, table)} "
                    f"without a {self._column} equality in its WHERE or join ON"
                )


def _parameter_keys(multiparams: Sequence[Mapping[str, Any]], params: Mapping[str, Any]) -> frozenset[str]:
    first = multiparams[0] if multiparams and isinstance(multiparams[0], Mapping) else {}
    return frozenset(str(key) for key in (*params, *first))


def _flatten(from_: FromClause, conditions: list[_Condition]) -> list[FromClause]:
    """The leaves of ``from_``, adding each join's ``ON`` to ``conditions`` with the leaves it filters."""
    if not isinstance(from_, Join):
        return [from_]
    left = _flatten(from_.left, conditions)
    right = _flatten(from_.right, conditions)
    if from_.full:
        filters: list[FromClause] = []
    elif from_.isouter:
        filters = right
    else:
        filters = [*left, *right]
    conditions.append(_Condition(from_.onclause, frozenset(filters)))
    return [*left, *right]


def _underlying_table(leaf: FromClause) -> Table | None:
    """The table ``leaf`` is, or the one an alias (of an alias…) of it names, unwrapped as the scoped seam does;
    ``None`` for a join, a subquery, or any other derived FROM."""
    while isinstance(leaf, Alias):
        leaf = leaf.element
    return leaf if isinstance(leaf, Table) else None


def _leaf_name(leaf: FromClause, table: Table) -> str:
    return f"{table.name} (as {leaf.name})" if isinstance(leaf, Alias) else table.name


def _conjuncts(condition: Any) -> Iterator[ClauseElement]:
    if condition is None:
        return
    if isinstance(condition, Grouping):
        yield from _conjuncts(condition.element)
    elif isinstance(condition, BooleanClauseList) and condition.operator is operators.and_:
        for clause in condition.clauses:
            yield from _conjuncts(clause)
    else:
        yield condition


def _equalities(condition: ClauseElement | None) -> Iterator[tuple[ClauseElement, ClauseElement]]:
    """The operands of each top-level equality in ``condition``."""
    for conjunct in _conjuncts(condition):
        if isinstance(conjunct, BinaryExpression) and conjunct.operator is operators.eq:
            yield conjunct.left, conjunct.right


def _key_leaf(side: ClauseElement, leaves: Iterable[FromClause], column: str) -> FromClause | None:
    """The leaf among ``leaves`` whose ``column`` column ``side`` is."""
    if not (isinstance(side, ColumnClause) and side.name == column):
        return None
    return next((leaf for leaf in leaves if side.table is leaf), None)


def _is_value(side: Any) -> bool:
    """Whether ``side`` is a bound value — a parameter or a literal, possibly grouped or cast — naming no column."""
    while isinstance(side, Grouping | Cast | TypeCoerce):
        side = side.clause if isinstance(side, Cast | TypeCoerce) else side.element
    return isinstance(side, BindParameter)
