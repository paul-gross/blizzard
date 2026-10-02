"""Shared ``LeaseTraceFacts`` builders for the runner tracing unit tests — facts built directly, no store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from blizzard.runner.domain.invocation_boundaries import InvocationBoundaryKind
from blizzard.runner.domain.tracing.facts import (
    BoundaryRow,
    LeaseClosureRow,
    LeaseContextRow,
    LeaseRow,
    LeaseTraceFacts,
    SpawnRow,
    UsageRow,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
LEASE_ID = "lease_1"
CHUNK_ID = "ch_1"
EPOCH = 1


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def spawn(row_id: int, seconds: int, *, session: str | None = None, identified: bool = True) -> SpawnRow:
    return SpawnRow(
        id=row_id,
        spawned_at=at(seconds),
        harness_id="claude-code",
        harness_version="2.1",
        session_id=session or f"sess-{row_id}",
        identified_at=at(seconds + 1) if identified else None,
    )


def boundary(
    row_id: int, generation: int, kind: InvocationBoundaryKind, opened: int, closed: int | None = None
) -> BoundaryRow:
    return BoundaryRow(row_id, generation, kind, at(opened), at(closed) if closed is not None else None)


def usage(row_id: int, generation: int, kind: str, seconds: int, **kw: object) -> UsageRow:
    fields: dict[str, object] = {
        "model": "claude-opus",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_tokens": 1000,
        "cache_create_tokens": 50,
        "cost_usd": 0.5,
        **kw,
    }
    return UsageRow(id=row_id, generation=generation, kind=kind, recorded_at=at(seconds), **fields)  # type: ignore[arg-type]


def make_facts(*, reason: str = "transitioned", closed: int = 100, **kwargs: object) -> LeaseTraceFacts:
    """One closed lease minted at ``at(0)``: a single identified spawn opened at ``at(1)`` unless overridden."""
    defaults: dict[str, object] = {
        "spawns": (spawn(1, 1),),
        "boundaries": (boundary(1, 1, "spawn", 1, closed),),
    }
    defaults.update(kwargs)
    return LeaseTraceFacts(
        lease=LeaseRow(LEASE_ID, CHUNK_ID, EPOCH, "r-1", at(0)),
        context=LeaseContextRow(
            graph_id="g1",
            node_id="g1-build",
            node_name="build",
            graph_name="flow",
            work_refs=("blizzard#745",),
            session_name="builder",
            resolved_model="opus",
            resolved_effort="high",
        ),
        closure=LeaseClosureRow(reason, at(closed)),
        **defaults,  # type: ignore[arg-type]
    )


def with_context(facts: LeaseTraceFacts, **kw: object) -> LeaseTraceFacts:
    return replace(facts, context=replace(facts.context, **kw))  # type: ignore[arg-type]
