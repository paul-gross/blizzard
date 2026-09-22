"""The usage/cost aggregate fold's one prose home (`bzh:one-prose-home`) — the nine
labeled columns, shared by the chunk-usage seam (``chunk_usage_store.py``) and the
analytics seam (``analytics_operational_store.py``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func

from blizzard.hub.store import schema as s


def usage_aggregate_columns() -> tuple[ColumnElement[Any], ...]:
    u = s.usage_facts
    # `UsageTotal.of_grouped_sums`'s `both_null_rows`: `coalesce` is non-null when either amount is.
    both_null_rows = func.count() - func.count(func.coalesce(u.c.cost_usd, u.c.estimated_cost_usd))
    return (
        func.coalesce(func.sum(u.c.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(u.c.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(u.c.cache_read_tokens), 0).label("cache_read_tokens"),
        func.coalesce(func.sum(u.c.cache_create_tokens), 0).label("cache_create_tokens"),
        func.coalesce(func.sum(u.c.cost_usd), 0.0).label("cost_usd"),
        func.coalesce(func.sum(u.c.estimated_cost_usd), 0.0).label("estimated_cost_usd"),
        func.count(u.c.estimated_cost_usd).label("estimated_rows"),
        both_null_rows.label("both_null_rows"),
        (func.count() - func.count(u.c.cost_usd)).label("null_cost_rows"),
    )
