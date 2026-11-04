"""Helpers for the completion rules tests, shared with other test modules."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TypedDict, Unpack

from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    DecisionChoice,
    GateDecision,
    LeaseFact,
    MigrationFact,
)
from blizzard.hub.domain.execution.submissions import CheckOutcome, Completion, CompletionArtifact
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Choice, Edge, Node
from tests.support import make_graph

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


T1 = _T0 + timedelta(minutes=1)


def _node(
    node_id: str,
    name: str,
    *,
    graph_id: str = "gr_a",
    executor: Executor = Executor.RUNNER,
    judged_by: JudgedBy = JudgedBy.WORKER,
    choices: list[Choice] | None = None,
) -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name=name,
        executor=executor,
        prompt="p",
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=judged_by,
        retries_max=None,
        retries_exhausted=None,
        choices=choices if choices is not None else [],
    )


BUILD = _node(
    "nd_build",
    "build",
    choices=[
        Choice("ch_ship", "ship", "ship it"),
        Choice("ch_review", "review", "", requires_checks=True),
        Choice("ch_hand", "hand-off", ""),
        Choice("ch_gated_hand", "gated-hand-off", "", requires_checks=True),
        Choice("ch_lost", "lost", ""),
    ],
)


_REVIEW = _node("nd_review", "review", choices=[Choice("ch_ok", "ok", "")])


_LAND = _node("nd_land", "land", executor=Executor.HUB, choices=[Choice("ch_landed", "landed", "")])


_GATE = _node(
    "nd_gate", "gate", judged_by=JudgedBy.HUMAN, choices=[Choice("ch_yes", "yes", ""), Choice("ch_no", "no", "")]
)


GRAPH = make_graph(
    "gr_a",
    "work",
    entry_node_id="nd_build",
    nodes=[BUILD, _REVIEW, _LAND, _GATE],
    edges=[
        Edge("nd_build", "ch_ship", RESERVED_TERMINAL),
        Edge("nd_build", "ch_review", "review"),
        Edge("nd_build", "ch_hand", "graph:delivery", target_graph="delivery", model="opus"),
        Edge("nd_build", "ch_gated_hand", "graph:delivery", target_graph="delivery"),
        Edge("nd_build", "ch_lost", "nowhere"),
        Edge("nd_review", "ch_ok", "land"),
        Edge("nd_gate", "ch_yes", "review"),
        Edge("nd_gate", "ch_no", "graph:delivery", target_graph="delivery"),
    ],
)


DELIVERY = make_graph(
    "gr_d",
    "delivery",
    entry_node_id="nd_d_entry",
    nodes=[
        _node("nd_d_entry", "entry", graph_id="gr_d"),
        _node("nd_d_review", "review", graph_id="gr_d"),
        _node("nd_d_build", "build", graph_id="gr_d"),
        _node("nd_d_land", "land", graph_id="gr_d", executor=Executor.HUB),
    ],
)


CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_a", work_refs=[], minted_at=_T0)


AT_BUILD = ChunkFacts(minted=True, promoted=True, leases=[LeaseFact(epoch=1, minted_at=_T0)])


class _SubmissionFields(TypedDict, total=False):
    check_results: Sequence[CheckOutcome]
    artifacts: Sequence[CompletionArtifact]
    decision_id: str | None
    route_token: str | None
    lease_id: str | None


def completion_submission(
    choice: str, *, from_node_id: str = "nd_build", **fields: Unpack[_SubmissionFields]
) -> Completion:
    return Completion(choice=choice, epoch=1, runner_id="runner-a", from_node_id=from_node_id, **fields)


def gate_decision(
    *, chunk_id: str = "chk_1", node_id: str = "nd_gate", resolved: str | None = None, transitioned: bool = False
) -> GateDecision:
    return GateDecision(
        decision_id="dec_1",
        chunk_id=chunk_id,
        node_id=node_id,
        node_name="gate",
        epoch=1,
        choices=[DecisionChoice(name="yes", description=""), DecisionChoice(name="no", description="")],
        submitted_at=_T0,
        resolved_choice=resolved,
        transitioned=transitioned,
    )


def migration_fact(source: MigrationSource | None, executor: Executor = Executor.RUNNER) -> MigrationFact:
    return MigrationFact(
        from_node_id="nd_build",
        from_graph_id="gr_a",
        to_graph_id="gr_d",
        landed_node_id="nd_d_build",
        choice_name="hand-off",
        model=None,
        epoch=1,
        recorded_at=T1,
        landed_node_executor=executor,
        source=source,
    )
