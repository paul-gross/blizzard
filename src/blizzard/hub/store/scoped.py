"""The scoped store seam: connections and statement builders confined to one tenant.

``ScopedConnections`` is the connections object an adapter over a tenant-owned table holds
(``bzh:tenant-scoped-store``). It is a :class:`HubStoreConnections` — ``read``/``write`` keep
their shape — bound to one :class:`StoreScope`, and it builds statements that apply that scope's
tenant to every tenant-owned table they name:

- ``select(from_, *cols)`` puts ``tenant_id = <tenant>`` in ``WHERE`` for each tenant-owned table
  whose rows the read returns unconditionally — the table itself, or each side of an inner join and
  the preserved side of an outer one;
- ``scope_join(left, right, onclause, isouter=...)`` puts the right side's predicate in the join's
  ``ON``, so an outer join stays outer; the left side is scoped by the read selecting from it;
- ``exists(from_, *criteria)`` is a scoped, correlatable ``EXISTS``;
- ``insert(table)`` stamps ``tenant_id`` into every row's values; ``update``/``delete`` are confined
  to the tenant's rows.

Everything else composes from these: an alias is scoped like its table, a grouped subquery or a
``UNION`` arm is a scoped ``select``, and a FROM-less select wraps scoped subqueries.

A table is tenant-owned when it has a ``tenant_id`` column and is not on ``schema.GLOBAL_TABLES``
(:func:`is_tenant_owned`, the one place that test lives). A table outside that test passes through
every builder untouched.

Code that holds only the ``Connection`` a scoped ``read``/``write`` yielded — a shared helper, a
locked handle's sibling adapter — recovers the same builders with :func:`statements_of`. The scope
travels as an execution option on that ``Connection`` object, which is discarded at the end of the
block, never on the pooled driver connection behind it.

The stamp is also guarded at execute: a statement run through a scoped connection that writes a
tenant-owned table and carries ``tenant_id`` among its execute-time parameters is refused, since an
``executemany`` row's own ``tenant_id`` would override the builder's stamp."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine, Exists, event
from sqlalchemy.sql import Alias, ColumnElement, Delete, FromClause, Insert, Join, Select, TableClause, Update

from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.tenancy import StoreScope

TENANT_COLUMN = "tenant_id"

# The execution-option key a scoped ``read``/``write`` sets on the ``Connection`` it yields.
_SCOPE_OPTION = "blizzard_store_scope"


class UnscopedConnection(RuntimeError):
    """Raised when scoped builders are asked of a ``Connection`` no scoped seam yielded."""


class TenantKeySupplied(ValueError):
    """Raised when a write through a scoped connection passes ``tenant_id`` at execute time —
    the seam stamps the key, and an execute-time value would override the stamp."""

    def __init__(self, table: str) -> None:
        super().__init__(f"tenant_id passed at execute for {table}; the scoped insert stamps it")
        self.table = table


def _base_table(from_: FromClause) -> TableClause | None:
    """The table an alias (of an alias…) names, or the table itself; ``None`` for a join, a
    subquery, or any other derived FROM."""
    while isinstance(from_, Alias):
        from_ = from_.element
    return from_ if isinstance(from_, TableClause) else None


def is_tenant_owned(table: FromClause) -> bool:
    """Whether ``table`` (or the table an alias of it names) is tenant-owned: it has a
    ``tenant_id`` column and is not on ``GLOBAL_TABLES``. A join or a subquery is not a table and
    owns nothing itself."""
    base = _base_table(table)
    return base is not None and TENANT_COLUMN in base.c and base.name not in s.GLOBAL_TABLES


def _preserved(from_: FromClause) -> Iterator[FromClause]:
    """The tables (or aliases) in ``from_`` whose rows a read of it returns unconditionally: both
    sides of an inner join, only the left side of an outer join."""
    if isinstance(from_, Join):
        yield from _preserved(from_.left)
        if not from_.isouter:
            yield from _preserved(from_.right)
    elif _base_table(from_) is not None:
        yield from_


class ScopedStatements:
    """Statement builders bound to one :class:`StoreScope`; each applies the scope's tenant to
    every tenant-owned table it names."""

    def __init__(self, scope: StoreScope) -> None:
        self._scope = scope

    @property
    def scope(self) -> StoreScope:
        return self._scope

    def tenant_predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        """``tenant_id = <tenant>`` for each tenant-owned table a read of ``from_`` returns rows
        of unconditionally — empty for a global table or a derived FROM."""
        return [t.c[TENANT_COLUMN] == self._scope.tenant for t in _preserved(from_) if is_tenant_owned(t)]

    def select(self, from_: FromClause, *cols: Any) -> Select[Any]:
        """``SELECT cols`` (every column of ``from_`` when none are named) ``FROM from_``, confined
        to the tenant. ``from_`` may be a table, an alias, or a join :meth:`scope_join` built."""
        stmt: Select[Any] = sa.select(*cols).select_from(from_) if cols else sa.select(from_)
        predicates = self.tenant_predicates(from_)
        return stmt.where(*predicates) if predicates else stmt

    def exists(self, from_: FromClause, *criteria: ColumnElement[bool]) -> Exists:
        """``EXISTS (SELECT 1 FROM from_ WHERE criteria)`` confined to the tenant; it correlates
        to an enclosing query like any ``EXISTS``, and ``~`` makes it ``NOT EXISTS``."""
        return self.select(from_, sa.literal_column("1")).where(*criteria).exists()

    def scope_join(
        self, left: FromClause, right: FromClause, onclause: ColumnElement[bool], *, isouter: bool = False
    ) -> Join:
        """``left JOIN right ON onclause`` with the right side's tenant predicate in the ``ON`` —
        so a ``LEFT OUTER JOIN`` keeps its unmatched left rows. The left side is scoped by the
        :meth:`select` that reads the join."""
        return sa.join(left, right, sa.and_(onclause, *self.tenant_predicates(right)), isouter=isouter)

    def insert(self, table: TableClause) -> Insert:
        """``INSERT INTO table`` with ``tenant_id`` stamped into every row's values."""
        stmt = sa.insert(table)
        return stmt.values({TENANT_COLUMN: self._scope.tenant}) if is_tenant_owned(table) else stmt

    def update(self, table: TableClause) -> Update:
        """``UPDATE table`` confined to the tenant's rows."""
        stmt = sa.update(table)
        return stmt.where(table.c[TENANT_COLUMN] == self._scope.tenant) if is_tenant_owned(table) else stmt

    def delete(self, table: TableClause) -> Delete:
        """``DELETE FROM table`` confined to the tenant's rows."""
        stmt = sa.delete(table)
        return stmt.where(table.c[TENANT_COLUMN] == self._scope.tenant) if is_tenant_owned(table) else stmt


def statements_of(conn: Connection) -> ScopedStatements:
    """The scoped builders for ``conn``, a ``Connection`` a :class:`ScopedConnections` ``read`` or
    ``write`` yielded. Raises :class:`UnscopedConnection` for any other connection."""
    scope = conn.get_execution_options().get(_SCOPE_OPTION)
    if not isinstance(scope, StoreScope):
        raise UnscopedConnection("this connection carries no store scope; open it through ScopedConnections")
    return ScopedStatements(scope)


def _execute_time_rows(multiparams: Sequence[Any], params: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield params
    for item in multiparams:
        if isinstance(item, Mapping):
            yield item
        elif isinstance(item, Sequence) and not isinstance(item, str):
            yield from (row for row in item if isinstance(row, Mapping))


def _refuse_execute_time_tenant(
    conn: Connection,
    clauseelement: Any,
    multiparams: Sequence[Any],
    params: Mapping[str, Any],
    execution_options: Mapping[str, Any],
) -> None:
    """A ``before_execute`` listener on one scoped ``Connection``: an insert or update of a
    tenant-owned table may not carry ``tenant_id`` among its execute-time parameters."""
    if not isinstance(clauseelement, Insert | Update) or not is_tenant_owned(clauseelement.table):
        return
    if any(TENANT_COLUMN in row for row in _execute_time_rows(multiparams, params)):
        table = _base_table(clauseelement.table)
        raise TenantKeySupplied(table.name if table is not None else str(clauseelement.table))


class ScopedConnections(HubStoreConnections, ScopedStatements):
    """:class:`HubStoreConnections` bound to one tenant: ``read``/``write`` yield a connection
    :func:`statements_of` recovers this scope from, and the builders apply it. Obtained through
    :meth:`HubStoreConnections.scoped`."""

    def __init__(self, engine: Engine, errors: HubStoreErrorFactory, scope: StoreScope) -> None:
        HubStoreConnections.__init__(self, engine, errors)
        ScopedStatements.__init__(self, scope)

    def scoped(self, scope: StoreScope) -> ScopedConnections:
        """This object for its own scope; a scoped seam is never widened or moved to another
        tenant."""
        if scope != self.scope:
            raise ValueError(f"connections scoped to {self.scope.tenant} cannot be rescoped to {scope.tenant}")
        return self

    def _bind(self, conn: Connection) -> Connection:
        conn.execution_options(**{_SCOPE_OPTION: self.scope})
        event.listen(conn, "before_execute", _refuse_execute_time_tenant)
        return conn

    @contextmanager
    def read(self, operation: str) -> Iterator[Connection]:
        with super().read(operation) as conn:
            yield self._bind(conn)

    @contextmanager
    def write(self, operation: str, *, expect: tuple[type[BaseException], ...] = ()) -> Iterator[Connection]:
        with super().write(operation, expect=expect) as conn:
            yield self._bind(conn)
