"""The newest-fact-per-key read, shared by the stores that derive state off an
append-only fact table (package-private).

One group-by-max join, portable across backends (``bzh:sql-portable``): rows read grow with
the number of keys asked for, not with each key's fact history (``bzh:live-set-read``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Column, Select, Table, func, select


def newest_fact_select(table: Table, key: Column[Any], keys: Sequence[str], *columns: Column[Any]) -> Select:  # type: ignore[type-arg]
    """``columns`` of the single newest (highest ``id``) row of ``table`` per ``key`` value
    in ``keys`` — callers batch ``keys`` through ``id_batches``."""
    newest = select(func.max(table.c.id).label("id")).where(key.in_(keys)).group_by(key).subquery()
    return select(*columns).join(newest, table.c.id == newest.c.id)
