"""Shared ``StepFacts`` builders for the tracing unit tests — facts built directly, no store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.tracing.facts import EpochOwnerRecord, LeaseRecord, StepFacts, TransitionRecord

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def node(graph_id: str, name: str, executor: Executor = Executor.RUNNER) -> Node:
    return Node(
        node_id=f"{graph_id}-{name}",
        graph_id=graph_id,
        name=name,
        executor=executor,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def graph(graph_id: str, *names: str) -> Graph:
    return Graph(
        graph_id=graph_id,
        name="flow",
        entry_node_id=f"{graph_id}-{names[0]}",
        nodes=[node(graph_id, n) for n in names],
        edges=[],
        created_at=T0,
    )


G1 = graph("g1", "build", "review", "gate")
G2 = graph("g2", "build", "review", "gate")
GRAPHS = {"g1": G1, "g2": G2}


def make_facts(**kwargs: object) -> StepFacts:
    return StepFacts(chunk_id="ch_1", graphs=GRAPHS, pin_graph_id="g1", **kwargs)  # type: ignore[arg-type]


def to(graph: str, name: str, seconds: int, epoch: int, **kw: str | None) -> TransitionRecord:
    return TransitionRecord(epoch=epoch, recorded_at=at(seconds), graph_id=graph, to_node_id=f"{graph}-{name}", **kw)


def runner_epoch(epoch: int, seconds: int, runner: str | None = "r-1") -> dict[str, Any]:
    return {
        "lease_facts": (LeaseRecord(epoch, at(seconds)),),
        "epoch_owners": (EpochOwnerRecord(epoch, runner, at(seconds - 1)),),
    }


def merge(*parts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        for key, value in part.items():
            out[key] = out.get(key, ()) + value
    return out
