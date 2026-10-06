"""Shared ``LeaseTraceFacts`` builders for the runner tracing unit tests — facts built directly, no store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.tracing.facts import (
    BoundaryFact,
    CheckResultFact,
    ChecksRanFact,
    ContextSampleFact,
    LeaseClosureFact,
    LeaseContextFact,
    LeaseGrantFact,
    LeaseTraceFacts,
    NudgeFact,
    OverloadFact,
    ParkFact,
    ParkResumeFact,
    PauseParkFact,
    RunnerFact,
    SessionEndFact,
    SpawnFact,
    TakeoverFact,
    TokenUsageFact,
)
from blizzard.runner.transcripts.invocation_boundaries import InvocationBoundaryKind

T0 = datetime(2026, 1, 1, tzinfo=UTC)
#: The registration the store's identity row holds, which every fixture lease's spans carry.
REGISTERED = RunnerIdentity(runner_id="r-1", runner_name="r-claude", registered_at=T0)
LEASE_ID = "lease_1"
CHUNK_ID = "ch_1"
EPOCH = 1


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def spawn(row_id: int, seconds: int, *, session: str | None = None, identified: bool = True) -> SpawnFact:
    return SpawnFact(
        id=row_id,
        spawned_at=at(seconds),
        harness_id="claude-code",
        harness_version="2.1",
        session_id=session or f"sess-{row_id}",
        identified_at=at(seconds + 1) if identified else None,
    )


def boundary(
    row_id: int, generation: int, kind: InvocationBoundaryKind, opened: int, closed: int | None = None
) -> BoundaryFact:
    return BoundaryFact(row_id, generation, kind, at(opened), at(closed) if closed is not None else None)


def usage(row_id: int, generation: int, kind: str, seconds: int, **kw: object) -> TokenUsageFact:
    fields: dict[str, object] = {
        "model": "claude-opus",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_tokens": 1000,
        "cache_create_tokens": 50,
        "cost_usd": 0.5,
        **kw,
    }
    return TokenUsageFact(id=row_id, generation=generation, kind=kind, recorded_at=at(seconds), **fields)  # type: ignore[arg-type]


def make_facts(*, reason: str = "transitioned", closed: int = 100, **kwargs: object) -> LeaseTraceFacts:
    """One closed lease minted at ``at(0)``: a single identified spawn opened at ``at(1)`` unless overridden."""
    defaults: dict[str, object] = {
        "spawns": (spawn(1, 1),),
        "boundaries": (boundary(1, 1, "spawn", 1, closed),),
    }
    defaults.update(kwargs)
    return LeaseTraceFacts(
        lease=LeaseGrantFact(LEASE_ID, CHUNK_ID, EPOCH, at(0)),
        runner=RunnerFact(REGISTERED.runner_id, REGISTERED.runner_name),
        context=LeaseContextFact(
            graph_id="g1",
            node_id="g1-build",
            node_name="build",
            graph_name="flow",
            work_refs=("blizzard#745",),
            session_name="builder",
            resolved_model="opus",
            resolved_effort="high",
        ),
        closure=LeaseClosureFact(reason, at(closed)),
        **defaults,  # type: ignore[arg-type]
    )


def with_context(facts: LeaseTraceFacts, **kw: object) -> LeaseTraceFacts:
    return replace(facts, context=replace(facts.context, **kw))  # type: ignore[arg-type]


def busy_facts() -> LeaseTraceFacts:
    return make_facts(
        spawns=(spawn(1, 1), spawn(2, 41)),
        boundaries=(boundary(1, 1, "spawn", 1, 100), boundary(2, 2, "resume", 41, 100)),
        usage=(usage(1, 1, "spawn", 30), usage(2, 2, "resume", 90, estimated_cost_usd=0.1)),
        session_ends=(SessionEndFact(1, at(19)),),
        context_samples=(ContextSampleFact(1, at(10), 500),),
        parks=(ParkFact(1, "q1", at(20)),),
        park_resumes=(ParkResumeFact(1, "q1", at(40)),),
        pause_parks=(PauseParkFact(1, at(50)),),
        overloads=(OverloadFact(1, 2, 1, at(60), at(70)),),
        takeovers=(TakeoverFact("tko_1", at(80)),),
        nudges=(NudgeFact(1, EPOCH, at(85)),),
        check_results=(CheckResultFact(1, EPOCH, False),),
        checks_ran=(ChecksRanFact(1, EPOCH, at(95)),),
    )
