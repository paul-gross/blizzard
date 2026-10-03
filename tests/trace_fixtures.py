"""Shared ``StepFacts`` builders for the tracing unit tests — facts built directly, no store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.tracing.facts import (
    BounceRecord,
    ChunkCompletedRecord,
    ChunkStoppedRecord,
    DecisionRecord,
    DecisionResolutionRecord,
    EpochOwnerRecord,
    EscalationRecord,
    HubExecSlotRecord,
    HubPollRecord,
    LeaseRecord,
    MigrationRecord,
    PauseRecord,
    PrerequisiteMetRecord,
    PromotionRecord,
    QuestionRecord,
    RestartRecord,
    RouteCreatedRecord,
    RouteReleasedRecord,
    StepFacts,
    TransitionRecord,
)
from blizzard.hub.domain.work import UsageFact

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


def usage(epoch: int = 1, at_seconds: int = 12, **kw: Any) -> UsageFact:
    fields: dict[str, Any] = {
        "node_id": "g1-build",
        "epoch": epoch,
        "kind": "spawn",
        "model": "claude-x",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 1000,
        "cache_create_tokens": 10,
        "cost_usd": 0.5,
        "recorded_at": at(at_seconds),
        "harness_id": "claude-code",
        "harness_version": "2.0",
    }
    fields.update(kw)
    return UsageFact(**fields)


def hub_graph(graph_id: str = "g1") -> Graph:
    base = graph(graph_id, "build", "poll")
    nodes = [n if n.name != "poll" else Node(**{**n.__dict__, "executor": Executor.HUB}) for n in base.nodes]
    return Graph(**{**base.__dict__, "nodes": nodes})


def hub_facts(**extra: object) -> StepFacts:
    return StepFacts(
        chunk_id="ch_1",
        graphs={"g1": hub_graph(), "g2": G2},
        pin_graph_id="g1",
        **merge({"transitions": (to("g1", "poll", 5, 1),)}, runner_epoch(2, 90, runner=None), extra),
    )


def _gate(close: dict[str, tuple[object, ...]], *, resolved: bool = True) -> StepFacts:
    resolutions = (DecisionResolutionRecord("d1", at(40), choice="approve"),) if resolved else ()
    return make_facts(
        decisions=(DecisionRecord("d1", "g1-gate", 1, at(20)),),
        decision_resolutions=resolutions,
        usage=(usage(),),
        **merge(runner_epoch(1, 10), close),
    )


def _repinned() -> StepFacts:
    """Two graphs with different names: the step stands on the one it was pinned to, not the current pin."""
    old = replace(graph("g1", "build", "review", "gate"), name="legacy-flow")
    new = replace(graph("g2", "build", "review", "gate"), name="modern-flow")
    return StepFacts(
        chunk_id="ch_1",
        graphs={"g1": old, "g2": new},
        pin_graph_id="g2",
        migrations=(MigrationRecord(1, at(30), "g1", "g2", from_node_id="g1-build"),),
        transitions=(to("g2", "review", 90, 2),),
        **merge(runner_epoch(1, 10), runner_epoch(2, 50)),
    )


def scenarios() -> dict[str, StepFacts]:
    """Named facts covering every step kind and closing, shared by the span and row tests."""
    return {
        "runner-step": make_facts(
            work_refs=("blizzard#745",),
            work_sources=("blizzard",),
            usage=(usage(at_seconds=14),),
            transitions=(to("g1", "review", 30, 1, choice_name="pass"),),
            **runner_epoch(1, 10),
        ),
        "first-claim": make_facts(
            promotions=(PromotionRecord(at(2)),),
            pauses=(PauseRecord("p1", True, at(3)), PauseRecord("p2", False, at(6))),
            prerequisites_met=(PrerequisiteMetRecord(at(4)),),
            routes_created=(RouteCreatedRecord(at(20)),),
            transitions=(to("g1", "review", 90, 1),),
            **runner_epoch(1, 25),
        ),
        "gate-resolved-late-pickup": make_facts(
            decisions=(DecisionRecord("d1", "g1-gate", 1, at(20)),),
            decision_resolutions=(DecisionResolutionRecord("d1", at(40), choice="approve"),),
            transitions=(to("g1", "build", 500, 2, decision_id="d1", choice_name="approve"),),
            usage=(usage(),),
            **runner_epoch(1, 10),
        ),
        "gate-runner-imposed": make_facts(
            decisions=(DecisionRecord("d1", "g1-review", 1, at(20), imposed_by_runner_id="r-9"),),
            transitions=(to("g1", "build", 70, 2, decision_id="d1"),),
            **runner_epoch(1, 10),
        ),
        "gate-migrated": _gate({"migrations": (MigrationRecord(2, at(60), "g1", "g2", decision_id="d1"),)}),
        "gate-escalated": _gate({"escalations": (EscalationRecord(2, at(60), decision_id="d1"),)}),
        "gate-restarted": _gate({"restarts": (RestartRecord(2, at(60), "g1", "g1-build", decision_id="d1"),)}),
        "asks": make_facts(
            questions=(
                QuestionRecord("q1", 1, at(20), answered_at=at(15)),
                QuestionRecord("q2", 1, at(25), answered_at=at(28)),
                QuestionRecord("q3", 1, at(26)),
            ),
            transitions=(to("g1", "review", 40, 1),),
            **runner_epoch(1, 10),
        ),
        "pauses": make_facts(
            pauses=(
                PauseRecord("p0", True, at(2)),
                PauseRecord("p1", True, at(15)),
                PauseRecord("p2", False, at(18)),
                PauseRecord("p3", True, at(35)),
            ),
            transitions=(to("g1", "review", 40, 1),),
            **runner_epoch(1, 10),
        ),
        "hub-step": hub_facts(
            hub_polls=(
                HubPollRecord("h1", "g1-poll", 1, at(20)),
                HubPollRecord("h2", "g1-poll", 1, at(40)),
            ),
            hub_exec_slots=(HubExecSlotRecord("s1", "g1-poll", at(10), at(30)),),
            bounces=(BounceRecord(2, "conflict", at(90)),),
            transitions=(to("g1", "build", 100, 2),),
        ),
        "hub-escalated": hub_facts(
            bounces=(BounceRecord(2, "checks", at(95)),),
            escalations=(EscalationRecord(2, at(96)),),
        ),
        "migrated": make_facts(
            migrations=(MigrationRecord(1, at(30), "g1", "g2", from_node_id="g1-build", choice_name="upgrade"),),
            **runner_epoch(1, 10),
        ),
        "released": make_facts(route_released=(RouteReleasedRecord(at(30)),), **runner_epoch(1, 10)),
        "stopped": make_facts(chunk_stopped=(ChunkStoppedRecord(at(30)),), **runner_epoch(1, 10)),
        "completed": make_facts(chunk_completed=(ChunkCompletedRecord(at(30)),), **runner_epoch(1, 10)),
        "superseded": make_facts(**merge(runner_epoch(1, 10), runner_epoch(2, 50))),
        "restart": make_facts(
            decisions=(DecisionRecord("d1", "g1-gate", 1, at(20)),),
            restarts=(RestartRecord(2, at(40), "g1", "g1-build", decision_id="d1"),),
            epoch_owners=(
                EpochOwnerRecord(1, "r-1", at(9)),
                EpochOwnerRecord(2, None, at(40)),
                EpochOwnerRecord(3, "r-1", at(49)),
            ),
            lease_facts=(LeaseRecord(1, at(10)), LeaseRecord(3, at(50))),
            transitions=(to("g1", "review", 70, 3),),
        ),
        "released-claim": make_facts(
            epoch_owners=(EpochOwnerRecord(1, "r-1", at(5)), EpochOwnerRecord(2, "r-1", at(9))),
            lease_facts=(LeaseRecord(2, at(10)),),
            transitions=(to("g1", "review", 30, 2),),
        ),
        "repinned": _repinned(),
        "cost-mixed": make_facts(
            usage=(
                usage(epoch=1, at_seconds=11, cost_usd=0.5),
                usage(epoch=1, at_seconds=12, cost_usd=None, estimated_cost_usd=0.25),
                usage(epoch=1, at_seconds=13, cost_usd=None),
                usage(epoch=2, at_seconds=60, cost_usd=1.0, estimated_cost_usd=0.5),
            ),
            questions=(QuestionRecord("q1", 1, at(15), at(16)),),
            transitions=(to("g1", "review", 40, 1), to("g1", "gate", 90, 2)),
            **merge(runner_epoch(1, 10), runner_epoch(2, 50)),
        ),
        "cost-billed-only": make_facts(
            usage=(usage(at_seconds=12, cost_usd=0.5), usage(at_seconds=13, cost_usd=0.125)),
            transitions=(to("g1", "review", 40, 1),),
            **runner_epoch(1, 10),
        ),
        "cost-estimated-only": make_facts(
            usage=(usage(at_seconds=12, cost_usd=None, estimated_cost_usd=0.25),),
            transitions=(to("g1", "review", 40, 1),),
            **runner_epoch(1, 10),
        ),
        "cost-neither": make_facts(
            usage=(usage(at_seconds=12, cost_usd=None),),
            transitions=(to("g1", "review", 40, 1),),
            **runner_epoch(1, 10),
        ),
        "no-invocations": make_facts(transitions=(to("g1", "review", 40, 1),), **runner_epoch(1, 10)),
    }
