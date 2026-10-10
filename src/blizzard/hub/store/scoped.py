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
- ``insert(table)`` stamps ``tenant_id`` into its values — one row, or each row of an
  ``executemany``; ``insert_rows(table, rows)`` stamps each row of a multi-row ``VALUES``, and
  ``insert_from_select(table, names, select)`` stamps each selected row with the tenant as a literal
  column; a caller never supplies the key itself. ``update``/``delete`` are confined to the
  tenant's rows.

An insert into a table on :data:`SOLE_PROJECT_FALLBACK_TABLES` whose caller names no ``project_id``
gets the tenant's project when the tenant holds exactly one, and ``NULL`` — which the column refuses —
when it holds none or several (:meth:`ScopedStatements.sole_project_fallback`). A ``project_id`` the
caller names in the values always wins; one named among an insert's execute-time parameters is
refused. The fallback is an interim for the writers that do not name their project yet; no other
table and no other column is ever filled.

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
``executemany`` row's own ``tenant_id`` would override the builder's stamp.

A ``write`` opens by locking the scope's tenant row on the condition that the tenant is still
open — one statement that both takes the store's write lock and reads ``tenant_deletions`` under
it — and raises :class:`TenantClosed` when the tenant is closed (:class:`UnknownTenant` when no
tenant has the id). On SQLite that statement is the transaction's first write, so the driver's
deferred ``BEGIN`` precedes it and the read runs under the single writer lock: a close either
committed before it, and is seen, or waits for the writer's commit. The one write that cannot lock its
tenant's row is the one that creates it: ``founding`` opens by inserting that row instead. The writes a closed
tenant still takes — its deletion's drain and completion — open with ``teardown``, which locks the tenant's
deletion fact instead, on the condition that the deletion is still underway."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine, Exists, event
from sqlalchemy.sql import Alias, ColumnElement, Delete, FromClause, Insert, Join, Select, TableClause, Update

from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.tenancy import StoreScope, TenantClosed, UnknownTenant

TENANT_COLUMN = "tenant_id"
PROJECT_COLUMN = "project_id"

#: The project-owned tables whose ``project_id`` a scoped insert naming none fills with the tenant's only project.
#: ``config_changes`` is not among them: its ``project_id`` is null for a tenant-wide record.
SOLE_PROJECT_FALLBACK_TABLES: frozenset[str] = frozenset(
    {
        "chunks",
        "scopes",
        "routines",
        "findings",
        "finding_sets",
        "garden_proposals",
        "work_item_runs",
        "work_items",
    }
)

# The execution-option key a scoped ``read``/``write`` sets on the ``Connection`` it yields.
_SCOPE_OPTION = "blizzard_store_scope"


class UnscopedConnection(RuntimeError):
    """Raised when scoped builders are asked of a ``Connection`` no scoped seam yielded."""


class TeardownNotUnderway(LookupError):
    """Raised when a teardown write opens on a tenant with no deletion underway: never closed, or its deletion
    already completed."""

    def __init__(self, tenant: str) -> None:
        super().__init__(f"no deletion of tenant {tenant} is underway")
        self.tenant = tenant


class TenantKeySupplied(ValueError):
    """Raised when a caller supplies ``tenant_id`` for a tenant-owned table — in a builder's rows
    or column names, or among a scoped write's execute-time parameters. The seam stamps the key,
    and a caller's value would override the stamp."""

    def __init__(self, table: str) -> None:
        super().__init__(f"tenant_id supplied for {table}; the scoped insert stamps it")
        self.table = table


class ProjectKeyAtExecute(ValueError):
    """Raised when a scoped insert into a table on :data:`SOLE_PROJECT_FALLBACK_TABLES` carries ``project_id``
    among its execute-time parameters: the sole-project fallback in the statement's values would override it, so a
    caller names its project in ``values()`` or in ``insert_rows``' rows instead."""

    def __init__(self, table: str) -> None:
        super().__init__(f"project_id supplied at execute for {table}; name it in the insert's values")
        self.table = table


def _key_name(key: Any) -> str:
    """A values key's column name: the key itself, or the ``key`` of a column object."""
    return key if isinstance(key, str) else str(getattr(key, "key", key))


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

    def sole_project_fallback(self, table: TableClause, named: Iterable[str] = ()) -> dict[str, ColumnElement[Any]]:
        """The ``project_id`` an insert into ``table`` naming the columns ``named`` falls back to: the tenant's
        project as a scalar subquery — ``NULL`` unless the tenant holds exactly one — when ``table`` is on
        :data:`SOLE_PROJECT_FALLBACK_TABLES` and ``named`` lacks ``project_id``; nothing otherwise."""
        base = _base_table(table)
        if base is None or base.name not in SOLE_PROJECT_FALLBACK_TABLES or PROJECT_COLUMN not in base.c:
            return {}
        if PROJECT_COLUMN in named:
            return {}
        projects = s.projects
        only = (
            sa.select(sa.func.max(projects.c.project_id))
            .where(projects.c.tenant_id == self._scope.tenant)
            .having(sa.func.count() == 1)
            .scalar_subquery()
        )
        return {PROJECT_COLUMN: only}

    def insert(self, table: TableClause) -> Insert:
        """``INSERT INTO table`` with ``tenant_id`` stamped into every row's values, and the sole-project fallback
        beneath them — a ``project_id`` in a later ``values()`` or among the execute-time parameters wins."""
        stmt = sa.insert(table)
        if not is_tenant_owned(table):
            return stmt
        return stmt.values({TENANT_COLUMN: self._scope.tenant, **self.sole_project_fallback(table)})

    def insert_rows(self, table: TableClause, rows: Sequence[Mapping[Any, Any]]) -> Insert:
        """``INSERT INTO table VALUES (row), (row), …`` — one statement for all ``rows``, with
        ``tenant_id`` stamped into each. A row naming ``tenant_id`` itself is refused."""
        if not rows:
            raise ValueError(f"no rows to insert into {table.name}")
        if not is_tenant_owned(table):
            return sa.insert(table).values(list(rows))
        if any(_key_name(key) == TENANT_COLUMN for row in rows for key in row):
            raise TenantKeySupplied(table.name)
        tenant = self._scope.tenant
        stamped = []
        for row in rows:
            named = {_key_name(k): v for k, v in row.items()}
            stamped.append({**self.sole_project_fallback(table, named), **named, TENANT_COLUMN: tenant})
        return sa.insert(table).values(stamped)

    def insert_from_select(self, table: TableClause, names: Sequence[str], select: Select[Any]) -> Insert:
        """``INSERT INTO table (names…, tenant_id) SELECT …, <tenant>`` — ``select``'s columns fill
        ``names`` in order and the scope's tenant fills ``tenant_id`` as a literal column. The
        rows read are the caller's to confine: build ``select`` with :meth:`select`. ``names``
        naming ``tenant_id`` itself is refused."""
        if not is_tenant_owned(table):
            return sa.insert(table).from_select(names, select)
        if TENANT_COLUMN in names:
            raise TenantKeySupplied(table.name)
        tenant = sa.literal(self._scope.tenant, type_=table.c[TENANT_COLUMN].type)
        fallback = self.sole_project_fallback(table, names)
        return sa.insert(table).from_select(
            [*names, TENANT_COLUMN, *fallback], select.add_columns(tenant, *fallback.values())
        )

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
    tenant-owned table may not carry ``tenant_id`` among its execute-time parameters, nor an insert
    into a table on :data:`SOLE_PROJECT_FALLBACK_TABLES` ``project_id`` — the fallback in its values
    would override it."""
    if not isinstance(clauseelement, Insert | Update) or not is_tenant_owned(clauseelement.table):
        return
    rows = list(_execute_time_rows(multiparams, params))
    table = _base_table(clauseelement.table)
    if any(TENANT_COLUMN in row for row in rows):
        raise TenantKeySupplied(table.name if table is not None else str(clauseelement.table))
    if (
        isinstance(clauseelement, Insert)
        and table is not None
        and table.name in SOLE_PROJECT_FALLBACK_TABLES
        and any(PROJECT_COLUMN in row for row in rows)
    ):
        raise ProjectKeyAtExecute(table.name)


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
        """Like :meth:`HubStoreConnections.write`, opened by :meth:`_lock_open_tenant`, so the
        whole transaction runs under the tenant's lock and a closed tenant writes nothing."""
        with super().write(operation, expect=expect) as conn:
            self._lock_open_tenant(conn)
            yield self._bind(conn)

    @contextmanager
    def founding(self, operation: str, *, name: str, created_at: datetime, created_by: str) -> Iterator[Connection]:
        """Like :meth:`write`, opened by inserting the scope's ``tenants`` row instead of locking it: the write that
        brings its tenant into being. On connections joined to an open transaction it runs on that transaction, so
        each later write there locks the row this one inserted and everything commits or rolls back together."""
        with super().write(operation) as conn:
            conn.execute(
                sa.insert(s.tenants).values(
                    tenant_id=self.scope.tenant, name=name, created_at=created_at, created_by=created_by
                )
            )
            yield self._bind(conn)

    @contextmanager
    def teardown(self, operation: str) -> Iterator[Connection]:
        """Like :meth:`write`, opened by locking the scope's ``tenant_deletions`` row on the condition that its
        deletion is underway, instead of the tenant row on the condition that the tenant is open: the one write a
        closed tenant takes, which removes its rows. Concurrent teardowns of one tenant serialize on that row; a
        teardown that opens after the deletion completed raises :class:`TeardownNotUnderway`."""
        with super().write(operation, expect=(TeardownNotUnderway,)) as conn:
            deletions = s.tenant_deletions
            locked = conn.execute(
                sa.update(deletions)
                .where(deletions.c.tenant_id == self.scope.tenant, deletions.c.deleted_at.is_(None))
                .values(rows_removed=deletions.c.rows_removed)
            )
            if locked.rowcount != 1:
                raise TeardownNotUnderway(self.scope.tenant)
            yield self._bind(conn)

    def _lock_open_tenant(self, conn: Connection) -> None:
        """Lock the scope's tenant row if the tenant is open, as the transaction's first
        statement: a no-op ``UPDATE`` whose condition reads ``tenant_deletions``. On SQLite the
        ``UPDATE`` takes the single writer lock before its condition is read, so no close can
        commit between the check and the caller's writes. Only when it matches no row does a
        second read tell a closed tenant from an unknown one."""
        tenant = self.scope.tenant
        closed = sa.exists().where(s.tenant_deletions.c.tenant_id == tenant)
        locked = conn.execute(
            sa.update(s.tenants).where(s.tenants.c.tenant_id == tenant, ~closed).values(name=s.tenants.c.name)
        )
        if locked.rowcount == 1:
            return
        if conn.execute(sa.select(closed)).scalar_one():
            raise TenantClosed(tenant)
        raise UnknownTenant(tenant)
