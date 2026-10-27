"""The key-scoping core: statement builders that confine every keyed table they name to one key value.

A store whose tables are partitioned by an owning key describes that partition once as a
:class:`KeyScoping`: the key column, which tables are keyed by it, the execution option that exempts a statement from
the error a builder raises when a caller supplies the key itself. :mod:`blizzard.foundation.store.statement_guard`
checks executed statements against the same configuration.
:class:`KeyedStatements` is the builder set bound to one key value under that configuration.

A table is keyed when it has the key column and the configuration's ownership test admits it; any other table passes
through every builder untouched. An alias is scoped like the table it names."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import ColumnElement, Exists
from sqlalchemy.sql import Alias, ClauseElement, Delete, FromClause, Insert, Join, Select, TableClause, Update

from blizzard.foundation.roles import collaborator


class UnscopedKeyedStatement(Exception):
    """Raised before a statement executes when it reaches a keyed table without naming its key."""

    def __init__(self, violations: Sequence[str], statement: ClauseElement) -> None:
        super().__init__(f"{'; '.join(violations)} — in: {rendered(statement)}")
        self.violations = tuple(violations)


@collaborator
@dataclass(frozen=True)
class KeyScoping:
    """One partition of a store's tables by an owning key column.

    ``owns`` decides, of a table that has ``key_column``, whether it is keyed by it (a table lacking the column never
    is). ``owner`` names a keyed table in a refusal (``"tenant-owned"``). ``key_supplied`` builds the error raised,
    given a table's name, when a caller supplies the key a builder stamps. ``key_equality`` builds the
    ``<key> = <value>`` predicate from the key column and value; it must stay an ``=`` binary expression, which the
    statement guard reads."""

    key_column: str
    owns: Callable[[TableClause], bool]
    owner: str
    key_supplied: Callable[[str], Exception]
    key_equality: Callable[[ColumnElement[Any], Any], ColumnElement[bool]] = lambda column, key: column == key

    def is_keyed(self, from_: FromClause) -> bool:
        """Whether ``from_`` (or the table an alias of it names) is keyed. A join or a subquery owns nothing itself."""
        base = base_table(from_)
        return base is not None and self.key_column in base.c and self.owns(base)

    def refuse_supplied_key(self, statement: object, rows: Sequence[Mapping[str, Any]]) -> None:
        """Raise ``key_supplied`` when ``statement`` inserts into or updates a keyed table and one of the execute-time
        parameter ``rows`` carries the key — an ``executemany`` row's own key would override the builder's stamp."""
        if not isinstance(statement, Insert | Update) or not self.is_keyed(statement.table):
            return
        if any(self.key_column in row for row in rows):
            table = base_table(statement.table)
            raise self.key_supplied(table.name if table is not None else str(statement.table))


def rendered(statement: ClauseElement) -> str:
    """``statement`` as SQL, or its type's name when the default compiler cannot render it."""
    try:
        return str(statement)
    except Exception:  # a dialect-only construct the default compiler cannot render
        return type(statement).__name__


def key_name(key: Any) -> str:
    """A values key's column name: the key itself, or the ``key`` of a column object."""
    return key if isinstance(key, str) else str(getattr(key, "key", key))


def base_table(from_: FromClause) -> TableClause | None:
    """The table an alias (of an alias…) names, or the table itself; ``None`` for a join, a subquery, or any other
    derived FROM."""
    while isinstance(from_, Alias):
        from_ = from_.element
    return from_ if isinstance(from_, TableClause) else None


def execute_time_rows(multiparams: Sequence[Any], params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The parameter rows one execute binds: each ``executemany`` row, or its single parameter set."""
    rows: list[Mapping[str, Any]] = []
    for item in multiparams:
        if isinstance(item, Mapping):
            rows.append(item)
        elif isinstance(item, Sequence) and not isinstance(item, str):
            rows.extend(row for row in item if isinstance(row, Mapping))
    return rows or [params]


def _preserved(from_: FromClause) -> Iterator[FromClause]:
    """The tables (or aliases) in ``from_`` whose rows a read of it returns unconditionally: both sides of an inner
    join, only the left side of an outer join."""
    if isinstance(from_, Join):
        yield from _preserved(from_.left)
        if not from_.isouter:
            yield from _preserved(from_.right)
    elif base_table(from_) is not None:
        yield from_


class KeyedStatements:
    """Statement builders bound to one key value under one :class:`KeyScoping`; each applies the key to every keyed
    table it names. A subclass refines a keyed insert's rows or column names by overriding :meth:`_check_rows` and
    :meth:`_check_names`, which run after the supplied-key refusal."""

    def __init__(self, scoping: KeyScoping, key: Any) -> None:
        self._scoping = scoping
        self._key = key

    def key_predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        """``<key> = <value>`` for each keyed table a read of ``from_`` returns rows of unconditionally — empty for an
        unkeyed table or a derived FROM."""
        column, equality = self._scoping.key_column, self._scoping.key_equality
        return [equality(t.c[column], self._key) for t in _preserved(from_) if self._scoping.is_keyed(t)]

    def select(self, from_: FromClause, *cols: Any) -> Select[Any]:
        """``SELECT cols`` (every column of ``from_`` when none are named) ``FROM from_``, confined to the key.
        ``from_`` may be a table, an alias, or a join :meth:`scope_join` built."""
        stmt: Select[Any] = cast("Select[Any]", sa.select(*cols).select_from(from_) if cols else sa.select(from_))
        predicates = self.key_predicates(from_)
        return stmt.where(*predicates) if predicates else stmt

    def exists(self, from_: FromClause, *criteria: ColumnElement[bool]) -> Exists:
        """``EXISTS (SELECT 1 FROM from_ WHERE criteria)`` confined to the key; it correlates to an enclosing query
        like any ``EXISTS``, and ``~`` makes it ``NOT EXISTS``."""
        return self.select(from_, sa.literal_column("1")).where(*criteria).exists()

    def scope_join(
        self, left: FromClause, right: FromClause, onclause: ColumnElement[bool], *, isouter: bool = False
    ) -> Join:
        """``left JOIN right ON onclause`` with the right side's key predicate in the ``ON`` — so a ``LEFT OUTER
        JOIN`` keeps its unmatched left rows. The left side is scoped by the :meth:`select` that reads the join."""
        return sa.join(left, right, sa.and_(onclause, *self.key_predicates(right)), isouter=isouter)

    def insert(self, table: TableClause) -> Insert:
        """``INSERT INTO table`` with the key stamped into every row's values."""
        stmt = sa.insert(table)
        return stmt.values({self._scoping.key_column: self._key}) if self._scoping.is_keyed(table) else stmt

    def insert_rows(self, table: TableClause, rows: Sequence[Mapping[Any, Any]]) -> Insert:
        """``INSERT INTO table VALUES (row), (row), …`` — one statement for all ``rows``, with the key stamped into
        each. A row naming the key itself is refused."""
        if not rows:
            raise ValueError(f"no rows to insert into {table.name}")
        if not self._scoping.is_keyed(table):
            return sa.insert(table).values(list(rows))
        column = self._scoping.key_column
        if any(key_name(key) == column for row in rows for key in row):
            raise self._scoping.key_supplied(table.name)
        named = [{key_name(k): v for k, v in row.items()} for row in rows]
        self._check_rows(table, named)
        return sa.insert(table).values([{**row, column: self._key} for row in named])

    def insert_from_select(self, table: TableClause, names: Sequence[str], select: Select[Any]) -> Insert:
        """``INSERT INTO table (names…, <key>) SELECT …, <value>`` — ``select``'s columns fill ``names`` in order and
        the bound value fills the key as a literal column. The rows read are the caller's to confine: build ``select``
        with :meth:`select`. ``names`` naming the key itself is refused."""
        if not self._scoping.is_keyed(table):
            return sa.insert(table).from_select(names, select)
        column = self._scoping.key_column
        if column in names:
            raise self._scoping.key_supplied(table.name)
        self._check_names(table, names)
        value = sa.literal(self._key, type_=table.c[column].type)
        return sa.insert(table).from_select([*names, column], select.add_columns(value))

    def update(self, table: TableClause) -> Update:
        """``UPDATE table`` confined to the key's rows."""
        stmt = sa.update(table)
        if not self._scoping.is_keyed(table):
            return stmt
        return stmt.where(self._scoping.key_equality(table.c[self._scoping.key_column], self._key))

    def delete(self, table: TableClause) -> Delete:
        """``DELETE FROM table`` confined to the key's rows."""
        stmt = sa.delete(table)
        if not self._scoping.is_keyed(table):
            return stmt
        return stmt.where(self._scoping.key_equality(table.c[self._scoping.key_column], self._key))

    def _check_rows(self, table: TableClause, rows: Sequence[Mapping[str, Any]]) -> None:
        """Refuse ``insert_rows``' ``rows`` (keyed by column name) into keyed ``table``; none refused here."""

    def _check_names(self, table: TableClause, names: Sequence[str]) -> None:
        """Refuse ``insert_from_select``'s ``names`` into keyed ``table``; none refused here."""
