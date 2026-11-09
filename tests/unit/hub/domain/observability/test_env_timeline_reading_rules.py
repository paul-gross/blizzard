"""``EnvTimelineReading`` reads the facts a timeline or a step needs through its ports: the present read looks back a
route at a time for an environment its newest route left bare, at most ``LATEST_ROUNDS`` times, and the step read
lays the chunk's whole path over every route it took."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.graph.model import Choice, Graph, IReadGraphRepository, Node
from blizzard.hub.domain.observability.env_timeline import (
    LATEST_ROUNDS,
    ChunkTimelineFacts,
    EnvTimeline,
    EnvTimelineReading,
    Roster,
    RouteCreated,
    RouteReleased,
    StepTransition,
    TimelineRunner,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
ROSTER = Roster(runners={"rn": TimelineRunner("r-claude", "hs")}, hosts={"hs": "xps"})


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _node(node_id: str) -> Node:
    return Node(
        node_id=node_id,
        graph_id="gr",
        name=node_id,
        executor=Executor.RUNNER,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        choices=[Choice(choice_id=f"{node_id}-pass", name="pass", description="")],
    )


GRAPH = Graph(
    graph_id="gr", name="dwf", entry_node_id="build", nodes=[_node("build"), _node("verify")], edges=[], created_at=T0
)


@dataclass
class _Facts:
    created: list[RouteCreated]
    released: list[RouteReleased]
    chunks: dict[str, ChunkTimelineFacts]
    read: list[list[str]] = field(default_factory=list[list[str]])

    def routes(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        chunk_id: str | None = None,
        project: str | None = None,
    ) -> tuple[list[RouteCreated], list[RouteReleased]]:
        created = [c for c in self.created if chunk_id is None or c.chunk_id == chunk_id]
        return created, [r for r in self.released if chunk_id is None or r.chunk_id == chunk_id]

    def chunk_facts(self, chunk_ids: Sequence[str]) -> dict[str, ChunkTimelineFacts]:
        self.read.append(list(chunk_ids))
        return {cid: self.chunks[cid] for cid in chunk_ids if cid in self.chunks}

    def roster(self) -> Roster:
        return ROSTER


class _Graphs:
    def get_many(self, graph_ids: Sequence[str]) -> dict[str, Graph]:
        return {g: GRAPH for g in graph_ids if g == "gr"}


class _NoArtifacts:
    def load_artifacts_for(self, chunk_ids: Sequence[str]) -> dict[str, list[StoredArtifact]]:
        return {}


def _reading(facts: _Facts, *, now: float) -> EnvTimelineReading:
    return EnvTimelineReading(
        facts=facts,
        graphs=cast(IReadGraphRepository, _Graphs()),
        artifacts=cast(IReadChunkArtifactsRepository, _NoArtifacts()),
        clock=FixedClock(at(now)),
    )


def _held(chunk_id: str, created: float, released: float, seq: int) -> tuple[RouteCreated, RouteReleased]:
    return (
        RouteCreated(chunk_id, "rn", "ws", ("e1",), at(created), seq=seq),
        RouteReleased(chunk_id, at(released), seq=seq + 1),
    )


def _lane(timeline: EnvTimeline) -> list[tuple[str, str]]:
    return [
        (s.chunk_id, s.node_id)
        for h in timeline.hosts
        for ws in h.workspaces
        for env in ws.environments
        for s in env.segments
    ]


def _bare_routes_after(held_until: float, count: int) -> list[tuple[RouteCreated, RouteReleased]]:
    """``count`` routes onto ``e1``, each released the instant it was created — none holds a step."""
    return [_held(f"ch_bare_{i}", held_until + 1 + i, held_until + 1 + i, seq=1) for i in range(count)]


def test_an_environment_its_newest_route_left_bare_shows_the_step_one_route_back() -> None:
    held = _held("ch_held", 0, 10, seq=1)
    (bare,) = _bare_routes_after(10, 1)
    facts = _Facts(
        created=[held[0], bare[0]],
        released=[held[1], bare[1]],
        chunks={"ch_held": ChunkTimelineFacts("ch_held", graph_id="gr"), "ch_bare_0": ChunkTimelineFacts("ch_bare_0")},
    )

    timeline = _reading(facts, now=60).read_latest(since=T0)

    assert _lane(timeline) == [("ch_held", "build")]
    assert facts.read == [["ch_bare_0"], ["ch_held"]]


def test_the_present_read_looks_back_at_most_latest_rounds_routes() -> None:
    held = _held("ch_held", 0, 10, seq=1)
    within = _bare_routes_after(10, LATEST_ROUNDS - 1)
    chunks = {"ch_held": ChunkTimelineFacts("ch_held", graph_id="gr")} | {
        c.chunk_id: ChunkTimelineFacts(c.chunk_id) for c, _ in _bare_routes_after(10, LATEST_ROUNDS)
    }

    reached = _Facts([held[0], *(c for c, _ in within)], [held[1], *(r for _, r in within)], chunks)
    assert _lane(_reading(reached, now=60).read_latest(since=T0)) == [("ch_held", "build")]

    beyond = _bare_routes_after(10, LATEST_ROUNDS)
    capped = _Facts([held[0], *(c for c, _ in beyond)], [held[1], *(r for _, r in beyond)], chunks)
    assert _lane(_reading(capped, now=60).read_latest(since=T0)) == []
    assert len(capped.read) == LATEST_ROUNDS


def test_the_step_read_lays_the_chunks_path_over_every_route_it_took() -> None:
    first, second = _held("ch", 0, 8, seq=1), _held("ch", 9, 30, seq=3)
    chunk = ChunkTimelineFacts(
        "ch", graph_id="gr", transitions=(StepTransition(at(20), "gr", "verify", "pass", from_node_id="build"),)
    )
    facts = _Facts([first[0], second[0]], [first[1], second[1]], {"ch": chunk})

    detail = _reading(facts, now=60).step(chunk_id="ch", started_at=at(9))

    assert [(p.segment.node_id, p.segment.started_at, p.segment.ended_at) for p in detail.journey] == [
        ("build", at(0), at(8)),
        ("build", at(9), at(20)),
        ("verify", at(20), at(30)),
    ]
    assert detail.step.segment.started_at == at(9)
