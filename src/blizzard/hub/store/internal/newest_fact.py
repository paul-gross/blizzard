"""The newest-fact-per-key read, shared by the stores that derive state off an
append-only fact table (package-private).

One group-by-max join, portable across backends (``bzh:sql-portable``): rows read grow with
the number of keys asked for, not with each key's fact history (``bzh:newest-per-key-read``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Column, Select, Table, func, select


def newest_fact_select(
    table: Table,
    key: Column[Any],
    keys: Sequence[str] | None,
    *columns: Column[Any],  # type: ignore[type-arg]
) -> Select:  # type: ignore[type-arg]
    """``columns`` of the single newest (highest ``id``) row of ``table`` per ``key`` value
    in ``keys`` — callers batch ``keys`` through ``id_batches``. ``keys=None`` reads every key."""
    group = select(func.max(table.c.id).label("id"))
    if keys is not None:
        group = group.where(key.in_(keys))
    newest = group.group_by(key).subquery()
    return select(*columns).join(newest, table.c.id == newest.c.id)


def newest_retired_select(
    facts: Table,
    key: Column[Any],  # type: ignore[type-arg]
    keys: Sequence[str] | None = None,
    *columns: Column[Any],  # type: ignore[type-arg]
) -> Select:  # type: ignore[type-arg]
    """``key`` (then ``columns``) of every key whose newest lifecycle fact in ``facts`` reads
    retired — the one retirement read. ``keys=None`` reads every key; a supplied set restricts
    the group-by itself, and the result composes as a subquery."""
    return newest_fact_select(facts, key, keys, key, *columns).where(facts.c.retired.is_(True))
