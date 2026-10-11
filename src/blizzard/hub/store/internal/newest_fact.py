"""The newest-fact-per-key read, shared by the stores that derive state off an
append-only fact table (package-private).

One group-by-max join, portable across backends (``bzh:sql-portable``): rows read grow with
the number of keys asked for, not with each key's fact history (``bzh:newest-per-key-read``).

Both the outer read and its inner group-by are built through the caller's scope: a group-by
left hub-wide would pick another tenant's newest ``id`` for a shared key, and the scoped outer
join would then drop the caller's own newest fact."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Column, Select, Table, func

from blizzard.hub.store.scoped import ScopedStatements


def newest_fact_select(
    stmts: ScopedStatements,
    table: Table,
    key: Column[Any],
    keys: Sequence[str] | None,
    *columns: Column[Any],  # type: ignore[type-arg]
) -> Select:  # type: ignore[type-arg]
    """``columns`` of the single newest (highest ``id``) row of ``table`` per ``key`` value
    in ``keys``, within ``stmts``' scope — callers batch ``keys`` through ``id_batches``.
    ``keys=None`` reads every key."""
    group = stmts.select(table, func.max(table.c.id).label("id"))
    if keys is not None:
        group = group.where(key.in_(keys))
    newest = group.group_by(key).subquery()
    return stmts.select(table, *columns).join(newest, table.c.id == newest.c.id)


def newest_retired_select(
    stmts: ScopedStatements,
    facts: Table,
    key: Column[Any],  # type: ignore[type-arg]
    keys: Sequence[str] | None = None,
    *columns: Column[Any],  # type: ignore[type-arg]
) -> Select:  # type: ignore[type-arg]
    """``key`` (then ``columns``) of every key whose newest lifecycle fact in ``facts`` reads
    retired, within ``stmts``' scope — the one retirement read. ``keys=None`` reads every key;
    a supplied set restricts the group-by itself, and the result composes as a subquery."""
    return newest_fact_select(stmts, facts, key, keys, key, *columns).where(facts.c.retired.is_(True))
