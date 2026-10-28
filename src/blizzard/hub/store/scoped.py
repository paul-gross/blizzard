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

The seam knows no project. An insert into a project-keyed table (:func:`is_project_keyed`) names its
row's ``project_id`` — in its values, in each of its rows, or among its selected column names — or is
refused with :class:`ProjectKeyMissing`: ``insert_rows`` and ``insert_from_select`` refuse as they
build, and ``insert``, whose values arrive after it builds, when the statement executes. A row's
``project_id`` is checked for presence only, never filled and never filtered on.

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

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine, event
from sqlalchemy.sql import ColumnElement, FromClause, Insert, TableClause

from blizzard.foundation.store.key_scoping import (
    KeyedStatements,
    KeyScoping,
    base_table,
    execute_time_rows,
    key_name,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.tenancy import StoreScope, TenantClosed, UnknownTenant

TENANT_COLUMN = "tenant_id"
PROJECT_COLUMN = "project_id"

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


class ProjectKeyMissing(ValueError):
    """Raised when a scoped insert into a project-keyed table names no ``project_id`` — in a row of
    ``insert_rows``, among ``insert_from_select``'s names, or, at execute, in neither the statement's values
    nor each of its execute-time parameter rows. The seam never supplies a project: the caller names the
    one its row belongs to."""

    def __init__(self, table: str) -> None:
        super().__init__(f"no project_id named for {table}; a scoped insert names its row's project")
        self.table = table


#: The hub's partition of its tables by tenant: keyed by ``tenant_id``, every table carrying it but those on
#: ``GLOBAL_TABLES``.
TENANT_SCOPING = KeyScoping(
    key_column=TENANT_COLUMN,
    owns=lambda table: table.name not in s.GLOBAL_TABLES,
    owner="tenant-owned",
    key_supplied=TenantKeySupplied,
)


def is_tenant_owned(table: FromClause) -> bool:
    """Whether ``table`` (or the table an alias of it names) is tenant-owned: it has a
    ``tenant_id`` column and is not on ``GLOBAL_TABLES``. A join or a subquery is not a table and
    owns nothing itself."""
    return TENANT_SCOPING.is_keyed(table)


def is_project_keyed(table: FromClause) -> bool:
    """Whether ``table`` (or the table an alias of it names) is tenant-owned and keyed by a project: it has a
    ``project_id`` column that holds no ``NULL``. ``config_changes`` is not — its ``project_id`` is null for a
    tenant-wide record."""
    base = base_table(table)
    return (
        base is not None and is_tenant_owned(base) and PROJECT_COLUMN in base.c and not base.c[PROJECT_COLUMN].nullable
    )


def _entry_names(entry: Mapping[Any, Any] | Sequence[Any], table: TableClause) -> set[str]:
    """The column names one row of a multi-row ``VALUES`` fills: a mapping's keys, or the leading columns a
    positional row fills in order."""
    if isinstance(entry, Mapping):
        return {key_name(key) for key in entry}
    return {column.key for column in list(table.columns)[: len(entry)]}


def _names_project(insert: Insert, table: TableClause, rows: Sequence[Mapping[str, Any]]) -> bool:
    """Whether ``insert`` executed with the parameter ``rows`` names ``project_id`` for every row it writes: in its
    ``values()``, among its ``from_select()`` names, in each row of a multi-row ``VALUES``, or in each parameter row.

    SQLAlchemy keeps what a statement names itself only on private attributes until it compiles the statement."""
    named = [*(insert._values or {}), *(insert._select_names or ())]
    if any(key_name(key) == PROJECT_COLUMN for key in named):
        return True
    entries = [entry for batch in insert._multi_values for entry in batch]
    if entries and all(PROJECT_COLUMN in _entry_names(entry, table) for entry in entries):
        return True
    return all(PROJECT_COLUMN in row for row in rows)


class ScopedStatements(KeyedStatements):
    """Statement builders bound to one :class:`StoreScope` — the key-scoping core's builders
    (:class:`KeyedStatements`) under :data:`TENANT_SCOPING`; each applies the scope's tenant to every tenant-owned table
    it names. An insert into a project-keyed table names its row's ``project_id``: ``insert_rows`` and
    ``insert_from_select`` refuse with :class:`ProjectKeyMissing` as they build, and ``insert``, whose values arrive
    after it builds, when the statement executes."""

    def __init__(self, scope: StoreScope) -> None:
        KeyedStatements.__init__(self, TENANT_SCOPING, scope.tenant)
        self._scope = scope

    @property
    def scope(self) -> StoreScope:
        return self._scope

    def tenant_predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        """``tenant_id = <tenant>`` for each tenant-owned table a read of ``from_`` returns rows
        of unconditionally — empty for a global table or a derived FROM."""
        return self.key_predicates(from_)

    def _check_rows(self, table: TableClause, rows: Sequence[Mapping[str, Any]]) -> None:
        if is_project_keyed(table) and any(PROJECT_COLUMN not in row for row in rows):
            raise ProjectKeyMissing(table.name)

    def _check_names(self, table: TableClause, names: Sequence[str]) -> None:
        if is_project_keyed(table) and PROJECT_COLUMN not in names:
            raise ProjectKeyMissing(table.name)


def statements_of(conn: Connection) -> ScopedStatements:
    """The scoped builders for ``conn``, a ``Connection`` a :class:`ScopedConnections` ``read`` or
    ``write`` yielded. Raises :class:`UnscopedConnection` for any other connection."""
    scope = conn.get_execution_options().get(_SCOPE_OPTION)
    if not isinstance(scope, StoreScope):
        raise UnscopedConnection("this connection carries no store scope; open it through ScopedConnections")
    return ScopedStatements(scope)


def _check_execute_time_keys(
    conn: Connection,
    clauseelement: Any,
    multiparams: Sequence[Any],
    params: Mapping[str, Any],
    execution_options: Mapping[str, Any],
) -> None:
    """A ``before_execute`` listener on one scoped ``Connection``: an insert or update of a
    tenant-owned table may not carry ``tenant_id`` among its execute-time parameters, and an insert
    into a project-keyed table names ``project_id`` for every row it writes."""
    rows = execute_time_rows(multiparams, params)
    TENANT_SCOPING.refuse_supplied_key(clauseelement, rows)
    if not isinstance(clauseelement, Insert) or not is_tenant_owned(clauseelement.table):
        return
    table = base_table(clauseelement.table)
    if table is not None and is_project_keyed(table) and not _names_project(clauseelement, table, rows):
        raise ProjectKeyMissing(table.name)


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
        event.listen(conn, "before_execute", _check_execute_time_keys)
        return conn

    @contextmanager
    def read(self, operation: str) -> Generator[Connection, None, None]:
        with super().read(operation) as conn:
            yield self._bind(conn)

    @contextmanager
    def write(
        self, operation: str, *, expect: tuple[type[BaseException], ...] = ()
    ) -> Generator[Connection, None, None]:
        """Like :meth:`HubStoreConnections.write`, opened by :meth:`_lock_open_tenant`, so the
        whole transaction runs under the tenant's lock and a closed tenant writes nothing."""
        with super().write(operation, expect=expect) as conn:
            self._lock_open_tenant(conn)
            yield self._bind(conn)

    @contextmanager
    def founding(
        self, operation: str, *, name: str, created_at: datetime, created_by: str
    ) -> Generator[Connection, None, None]:
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
    def teardown(self, operation: str) -> Generator[Connection, None, None]:
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
