"""The newest-fact-per-key read, shared by the stores that derive state off an
append-only fact table (package-private).

Portable across backends (``bzh:sql-portable``): rows read grow with the keys asked for, not
with each key's fact history (``bzh:newest-per-key-read``). Group-by-max for the highest ``id``
per key; the negated anti-join for any other order; a plain ``EXISTS`` to ask only whether a
newer row exists. Every subquery, the competing row included, is built through the caller's
scope — a group-by left hub-wide would pick another tenant's newest ``id`` for a shared key."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Column, ColumnElement, Exists, FromClause, Select, Table, func, tuple_

from blizzard.hub.store.scoped import ScopedStatements


def newest_fact_select(
    stmts: ScopedStatements,
    table: Table,
    key: Column[Any],
    keys: Sequence[str] | Select | None,  # type: ignore[type-arg]
    *columns: Column[Any],  # type: ignore[type-arg]
) -> Select:  # type: ignore[type-arg]
    """``columns`` of the single newest (highest ``id``) row of ``table`` per ``key`` value
    in ``keys``, within ``stmts``' scope — callers batch ``keys`` through ``id_batches``, or
    pass a scalar select of key values. ``keys=None`` reads every key."""
    group = stmts.select(table, func.max(table.c.id).label("id"))
    if keys is not None:
        group = group.where(key.in_(keys))
    newest = group.group_by(key).subquery()
    return stmts.select(table, *columns).join(newest, table.c.id == newest.c.id)


def newest_retired_select(
    stmts: ScopedStatements,
    facts: Table,
    key: Column[Any],  # type: ignore[type-arg]
    keys: Sequence[str] | Select | None = None,  # type: ignore[type-arg]
    *columns: Column[Any],  # type: ignore[type-arg]
) -> Select:  # type: ignore[type-arg]
    """``key`` (then ``columns``) of every key whose newest lifecycle fact in ``facts`` reads
    retired, within ``stmts``' scope — the one retirement read. ``keys=None`` reads every key;
    a supplied set restricts the group-by itself, and the result composes as a subquery."""
    return newest_fact_select(stmts, facts, key, keys, key, *columns).where(facts.c.retired.is_(True))


def newer_row_exists(
    stmts: ScopedStatements,
    rival_from: FromClause,
    *,
    partition: Sequence[tuple[ColumnElement[Any], ColumnElement[Any]]],
    order: Sequence[tuple[ColumnElement[Any], ColumnElement[Any]]],
    where: ColumnElement[bool] | None = None,
) -> Exists:
    """``EXISTS`` a row of ``rival_from`` (the competing alias, or a scoped join of aliases) in the
    same partition as the outer row and strictly after it in ``order``; negated, it keeps the newest.
    ``partition`` and ``order`` pair each outer column with its competing one; ``order`` must be a
    total order, compared as one row value. ``where`` filters the competing row like the outer."""
    criteria = [rival == outer for outer, rival in partition]
    criteria.append(tuple_(*(rival for _, rival in order)) > tuple_(*(outer for outer, _ in order)))
    if where is not None:
        criteria.append(where)
    return stmts.exists(rival_from, *criteria)
