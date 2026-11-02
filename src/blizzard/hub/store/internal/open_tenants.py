"""The statement builders behind every cross-tenant read: closed tenants are left out because the builder does it,
not because each statement remembers to (package-private).

A tenant is closed when it has a ``tenant_deletions`` row, whether its drain is still running or done. A row naming no
tenant belongs to none, so it is left out too: ``NOT IN`` alone would admit it while ``tenant_deletions`` is empty.

:data:`OPEN_TENANTS` builds a statement that confines every tenant-owned table it returns rows of unconditionally —
the table itself, each side of an inner join, the preserved side of an outer one — to the open tenants, the same walk
:class:`blizzard.hub.store.scoped.ScopedConnections` makes for one tenant. A subquery built through it is confined the
same way, so a row tied to an open tenant's row is itself an open tenant's. A global table passes through untouched.

:data:`ALL_TENANTS` has the same surface and applies no predicate: the named opt-out of a read that deliberately sees
closed tenants — a change probe a closed tenant must still move, a principal resolved so that a refusal can name its
closed tenant, tenant administration's own reads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ColumnElement, Exists
from sqlalchemy.sql import CompoundSelect, FromClause, Join, Select

from blizzard.foundation.store.key_scoping import preserved
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import TENANT_COLUMN, is_tenant_owned


def open_tenant_predicate(from_: FromClause) -> ColumnElement[bool]:
    """``from_``'s rows that belong to an open tenant."""
    tenant = from_.c[TENANT_COLUMN]
    return sa.and_(tenant.is_not(None), tenant.not_in(sa.select(s.tenant_deletions.c.tenant_id)))


class _Statements:
    """Read-statement builders over every tenant's rows; a subclass names which of them it admits."""

    def predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        """The predicate for each tenant-owned table a read of ``from_`` returns rows of unconditionally."""
        raise NotImplementedError

    def select(self, from_: FromClause, *cols: Any) -> Select[Any]:
        """``SELECT cols`` (every column of ``from_`` when none are named) ``FROM from_``. ``from_`` may be a table,
        an alias, or a join :meth:`join` built."""
        stmt: Select[Any] = sa.select(*cols).select_from(from_) if cols else sa.select(from_)
        predicates = self.predicates(from_)
        return stmt.where(*predicates) if predicates else stmt

    def exists(self, from_: FromClause, *criteria: ColumnElement[bool]) -> Exists:
        """``EXISTS (SELECT 1 FROM from_ WHERE criteria)``; it correlates to an enclosing query like any ``EXISTS``,
        and ``~`` makes it ``NOT EXISTS``."""
        return self.select(from_, sa.literal_column("1")).where(*criteria).exists()

    def join(
        self, left: FromClause, right: FromClause, onclause: ColumnElement[bool], *, isouter: bool = False
    ) -> Join:
        """``left JOIN right ON onclause`` with the right side's predicate in the ``ON``, so a ``LEFT OUTER JOIN``
        keeps its unmatched left rows. The left side is confined by the :meth:`select` that reads the join."""
        return sa.join(left, right, sa.and_(onclause, *self.predicates(right)), isouter=isouter)

    def union(self, *arms: Select[Any]) -> CompoundSelect[Any]:
        """The ``UNION`` of ``arms``, each built through this builder."""
        first, *rest = arms
        return first.union(*rest)

    def union_all(self, *arms: Select[Any]) -> CompoundSelect[Any]:
        """The ``UNION ALL`` of ``arms``, each built through this builder."""
        return sa.union_all(*arms)

    def confine(self, statement: Select[Any]) -> Select[Any]:
        """``statement``, a finished select, with the predicate of each table it returns rows of unconditionally —
        join sides included."""
        predicates = [p for from_ in statement.get_final_froms() for p in self.predicates(from_)]
        return statement.where(*predicates) if predicates else statement

    def within_chunks(
        self, table: FromClause, chunk_id: ColumnElement[Any], chunk_ids: Sequence[str]
    ) -> ColumnElement[bool]:
        """``table``'s rows naming one of ``chunk_ids`` by ``chunk_id``, qualified by those chunks' own tenants: a
        chunk's rows share its tenant, and naming it lets the read seek the table's tenant-led chunk index rather
        than walk the table. The qualifier only narrows: the table's predicate comes from the :meth:`select` that
        reads it."""
        chunk_tenants = self.select(s.chunks, s.chunks.c.tenant_id).where(s.chunks.c.chunk_id.in_(chunk_ids))
        return sa.and_(table.c[TENANT_COLUMN].in_(chunk_tenants), chunk_id.in_(chunk_ids))


class OpenTenantStatements(_Statements):
    """Builders that confine every tenant-owned table to the open tenants."""

    def predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        return [open_tenant_predicate(t) for t in preserved(from_) if is_tenant_owned(t)]


class AllTenantStatements(_Statements):
    """The opt-out: the same builders, with closed tenants' rows admitted."""

    def predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        return []


OPEN_TENANTS = OpenTenantStatements()
ALL_TENANTS = AllTenantStatements()
