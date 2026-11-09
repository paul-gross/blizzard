"""The environment timeline — which chunk held each runner environment, and how long it spent in each node step.

A chunk's routes say which runner, workspace and environments held it and when; its transitions say which node it sat
at and when it moved. Clipping the node steps to each route's tenure lays every step on the environment that held the
chunk at the time. Pure: :class:`EnvTimelineFacts` in, :class:`EnvTimeline` or :class:`StepDetail` out;
:class:`EnvTimelineReading` reads the facts through the two ports and derives."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import MigrationFact, UsageTotal, open_escalation_of
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Graph, IReadGraphRepository

#: The widest window one timeline read answers; an older ``since`` is narrowed to this far before the window's end.
MAX_WINDOW = timedelta(days=14)

#: How far before its step's first instant a lease may be minted and still be the step's own — the mint and the
#: transition that opens the step are recorded by different writers.
LEASE_SLACK = timedelta(seconds=5)

#: How many routes back per environment the present read looks for one that held a step.
LATEST_ROUNDS = 4


@domain_model
@dataclass(frozen=True)
class RouteCreated:
    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: tuple[str, ...]
    created_at: datetime
    seq: int


@domain_model
@dataclass(frozen=True)
class RouteReleased:
    chunk_id: str
    released_at: datetime
    seq: int


@domain_model
@dataclass(frozen=True)
class RouteSpan:
    """One route's tenure: ``released_at`` is ``None`` while the route still holds the chunk."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: tuple[str, ...]
    created_at: datetime
    released_at: datetime | None


@domain_model
@dataclass(frozen=True)
class StepTransition:
    """A move of the chunk into ``to_node_id`` of ``graph_id``. ``from_graph_id`` is set only on a move across
    graphs: the graph ``from_node_id`` is a node of. ``to_node_id`` is ``None`` on a move across graphs whose landing
    the hub cannot place: it ends the step it left and opens none."""

    recorded_at: datetime
    graph_id: str
    to_node_id: str | None
    choice_name: str | None
    epoch: int | None = None
    from_node_id: str | None = None
    from_graph_id: str | None = None


@domain_model
@dataclass(frozen=True)
class StepMigration:
    """A move of the chunk onto ``to_graph_id``, out of ``from_node_id`` of ``from_graph_id``. ``landed_node_id`` is
    ``None`` on a move recorded without its landing node: it landed where the landing rule puts it
    (:meth:`~blizzard.hub.domain.chunk.model.MigrationFact.landing_node`)."""

    recorded_at: datetime
    from_graph_id: str
    to_graph_id: str
    from_node_id: str | None
    landed_node_id: str | None
    choice_name: str | None
    epoch: int | None = None

    def transition(self, graphs: Mapping[str, Graph]) -> StepTransition:
        """The move as a transition out of the step it left. One that names no landing node lands by the landing
        rule, resolved against both graphs; when the hub knows no ``to_graph_id``, or cannot resolve the
        ``from_node_id`` it names on ``from_graph_id``, it lands nowhere the hub can place — ``to_node_id`` is
        ``None`` — rather than on a guess."""
        return StepTransition(
            self.recorded_at,
            self.to_graph_id,
            self.landed_node_id if self.landed_node_id is not None else self._landing(graphs),
            self.choice_name,
            self.epoch,
            from_node_id=self.from_node_id,
            from_graph_id=self.from_graph_id,
        )

    def _landing(self, graphs: Mapping[str, Graph]) -> str | None:
        target = graphs.get(self.to_graph_id)
        if target is None:
            return None
        if self.from_node_id is None:
            return MigrationFact.landing_node(target, None)
        source = graphs.get(self.from_graph_id)
        left = source.node_by_id(self.from_node_id) if source is not None else None
        return MigrationFact.landing_node(target, left.name) if left is not None else None


@domain_model
@dataclass(frozen=True)
class StepEscalation:
    """An escalation to a human, recorded under ``epoch`` — the fence of the session or hub step that escalated."""

    recorded_at: datetime
    cause: str | None
    detail: str | None = None
    epoch: int | None = None


@domain_model
@dataclass(frozen=True)
class StepUsage:
    """One harness invocation's usage: ``kind`` is ``spawn`` (the step's session), ``resume`` (the session picked
    back up) or ``judge`` (choosing its exit)."""

    node_id: str
    recorded_at: datetime
    cost_usd: float | None
    estimated_cost_usd: float | None
    model: str | None = None
    harness_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    kind: str = "spawn"
    epoch: int | None = None

    def cost_partial(self) -> bool:
        """Whether the invocation carries neither a billed nor an estimated amount."""
        return self.cost_usd is None and self.estimated_cost_usd is None


@domain_model
@dataclass(frozen=True)
class StepLease:
    """A fence minted for the chunk: the hub's own — ``hub`` — for a step it runs itself, else a runner's. A runner's
    names its session's lease in ``lease_id``, or none when the runner took the chunk over on a forced entry; the
    hub's never names one."""

    epoch: int
    lease_id: str | None
    minted_at: datetime
    hub: bool = False


@domain_model
@dataclass(frozen=True)
class StepArtifact:
    """One artifact a node recorded under ``epoch``: its store key, and when it was recorded (``None`` when its id
    carries no instant)."""

    key: str
    node_id: str
    epoch: int
    recorded_at: datetime | None


@domain_model
@dataclass(frozen=True)
class ChunkTimelineFacts:
    """What the timeline reads about one chunk. ``transitions`` are its moves within a graph and ``migrations`` its
    moves across graphs; ``endings`` holds every instant it completed or was stopped; ``requeues`` and ``restarts``
    every instant it was requeued or restarted."""

    chunk_id: str
    title: str | None = None
    project_id: str | None = None
    graph_id: str | None = None
    endings: tuple[datetime, ...] = ()
    transitions: tuple[StepTransition, ...] = ()
    migrations: tuple[StepMigration, ...] = ()
    escalations: tuple[StepEscalation, ...] = ()
    usage: tuple[StepUsage, ...] = ()
    leases: tuple[StepLease, ...] = ()
    requeues: tuple[datetime, ...] = ()
    restarts: tuple[datetime, ...] = ()

    def open_escalation(self) -> StepEscalation | None:
        """The chunk's escalation still waiting on a human (:func:`~blizzard.hub.domain.chunk.model.open_escalation_of`)
        — its endings are its completions, stops and arrivals at the terminal node."""
        terminal = (t.recorded_at for t in self.transitions if t.to_node_id == RESERVED_TERMINAL)
        return open_escalation_of(
            self.escalations,
            mints=(le.minted_at for le in self.leases),
            requeues=self.requeues,
            restarts=self.restarts,
            endings=(*self.endings, *terminal),
        )

    def movements(self, graphs: Mapping[str, Graph]) -> list[StepTransition]:
        """Every move of the chunk, within a graph or across one, oldest first — a move across graphs landed by
        :meth:`StepMigration.transition`, kept even when it lands nowhere the hub can place, so it still ends the
        step it left."""
        moved = (m.transition(graphs) for m in self.migrations)
        return sorted((*self.transitions, *moved), key=lambda t: t.recorded_at)


@domain_model
@dataclass(frozen=True)
class TimelineRunner:
    name: str
    host_id: str | None


@domain_model
@dataclass(frozen=True)
class Roster:
    """Every registered runner by id, and every host's name by id."""

    runners: Mapping[str, TimelineRunner]
    hosts: Mapping[str, str]


@domain_model
@dataclass(frozen=True)
class EnvTimelineFacts:
    routes: tuple[RouteSpan, ...]
    chunks: Mapping[str, ChunkTimelineFacts]
    roster: Roster
    graphs: Mapping[str, Graph]


@domain_model
@dataclass(frozen=True)
class EnvSegment:
    """One node step a chunk spent in one environment. ``ended_at`` is ``None`` while the chunk is still there.

    ``failed`` holds when the step left by any choice but its node's first-declared one — the node's happy path —
    or escalated to a human; ``escalation_open`` when that escalation still waits on one. ``executor`` is where the
    node runs, ``None`` for a node on no graph the hub knows. ``epoch`` is the fence the step ran under — a runner
    mint's or the hub's own — and ``lease_id`` the session's runner lease. Their null patterns: both ``None`` — a
    runner step whose session the hub never heard minted, with no transcript to read; ``epoch`` set and ``lease_id``
    ``None`` — a hub step, or a runner session that took the chunk over on a forced entry, whose transcript reads by
    ``epoch``."""

    chunk_id: str
    title: str | None
    project_id: str | None
    runner_id: str
    runner_name: str
    graph_id: str
    graph_name: str | None
    node_id: str
    node_name: str
    started_at: datetime
    ended_at: datetime | None
    exit_choice: str | None
    failed: bool
    escalation: str | None
    cost_usd: float
    cost_estimated: bool
    executor: Executor | None = None
    escalation_open: bool = False
    epoch: int | None = None
    lease_id: str | None = None


@domain_model
@dataclass(frozen=True)
class EnvLane:
    environment_id: str
    segments: tuple[EnvSegment, ...]


@domain_model
@dataclass(frozen=True)
class WorkspaceLanes:
    workspace_id: str
    environments: tuple[EnvLane, ...]


@domain_model
@dataclass(frozen=True)
class HostLanes:
    """``host_id`` is ``None`` for runners on no host record."""

    host_id: str | None
    host_name: str | None
    runner_names: tuple[str, ...]
    workspaces: tuple[WorkspaceLanes, ...]


@domain_model
@dataclass(frozen=True)
class EnvTimeline:
    since: datetime
    until: datetime
    generated_at: datetime
    hosts: tuple[HostLanes, ...] = field(default_factory=tuple[HostLanes, ...])


#: An environment's place: its runner's host, its workspace, and its own id.
EnvKey = tuple[str | None, str, str]


def window_start(since: datetime, end: datetime) -> datetime:
    """``since``, narrowed to at most :data:`MAX_WINDOW` before ``end``."""
    return max(since, end - MAX_WINDOW)


def route_spans(created: Iterable[RouteCreated], released: Iterable[RouteReleased]) -> list[RouteSpan]:
    """Each route paired with the first release of its chunk after it, by the per-chunk route counter."""
    releases: dict[str, list[RouteReleased]] = defaultdict(list)
    for r in released:
        releases[r.chunk_id].append(r)
    spans: list[RouteSpan] = []
    for c in created:
        after = min((r for r in releases[c.chunk_id] if r.seq > c.seq), key=lambda r: r.seq, default=None)
        spans.append(
            RouteSpan(
                chunk_id=c.chunk_id,
                runner_id=c.runner_id,
                workspace_id=c.workspace_id,
                environment_ids=c.environment_ids,
                created_at=c.created_at,
                released_at=after.released_at if after is not None else None,
            )
        )
    return spans


def overlapping(spans: Iterable[RouteSpan], since: datetime, until: datetime) -> list[RouteSpan]:
    return [s for s in spans if s.created_at < until and (s.released_at is None or s.released_at > since)]


def _node_name(graph: Graph | None, node_id: str) -> str:
    node = graph.node_by_id(node_id) if graph is not None else None
    return node.name if node is not None else node_id


def _happy_choice(graph: Graph | None, node_id: str) -> str | None:
    node = graph.node_by_id(node_id) if graph is not None else None
    return node.choices[0].name if node is not None and node.choices else None


def _steps(chunk: ChunkTimelineFacts, graphs: Mapping[str, Graph], opened: datetime) -> list[StepTransition]:
    """The chunk's node steps, each opened by the transition into it — led by its entry step, which no transition
    opens: a chunk starts on its graph's entry node, there from when the route ``opened`` until its first
    transition."""
    steps = chunk.movements(graphs)
    first = steps[0] if steps else None
    if first is not None:
        graph_id = first.from_graph_id or first.graph_id
        entry = first.from_node_id
    else:
        graph_id = chunk.graph_id
        graph = graphs.get(graph_id) if graph_id is not None else None
        entry = graph.entry_node_id if graph is not None else None
    if graph_id is None or entry is None or (first is not None and first.recorded_at <= opened):
        return steps
    return [StepTransition(opened, graph_id, entry, None), *steps]


def _tenure_end(span: RouteSpan, chunk: ChunkTimelineFacts) -> datetime | None:
    """When ``span`` stopped holding the chunk: its release, else the chunk's first ending after it opened."""
    if span.released_at is not None:
        return span.released_at
    return min((at for at in chunk.endings if at >= span.created_at), default=None)


def _node_executor(graph: Graph | None, node_id: str) -> Executor | None:
    node = graph.node_by_id(node_id) if graph is not None else None
    return node.executor if node is not None else None


@domain_model
@dataclass(frozen=True)
class StepWindow:
    """One step of a route, as its fence is chosen: ``[start, end)`` — ``end`` is ``None`` while it runs —, the epoch
    its exit transition is fenced by, and where its node runs."""

    start: datetime
    end: datetime | None
    exit_epoch: int | None
    executor: Executor | None


def step_leases(steps: Sequence[StepWindow], leases: Sequence[StepLease]) -> list[StepLease | None]:
    """The fence that ran each of a route's ``steps``, oldest first. A hub step runs only one of the hub's own mints,
    a runner step only a runner's lease, and no fence runs two steps.

    A step's own is first the one its exit is fenced by — or, with no exit yet, the newest — when minted inside it
    or up to :data:`LEASE_SLACK` before ``start``: the mint that opens a step may be recorded just before the
    transition into it. A step with none of its own takes the newest no step claimed that way minted inside
    ``[start, end)`` itself — a step held across routes ran a lease on each, one its retries exhausted on the first
    before its exit's own on the next —, so a lease minted just before a step opens is only ever that step's.
    ``None`` when there is none: a runner step whose session the hub never heard minted."""

    def mine(step: StepWindow, taken: Collection[int]) -> list[StepLease]:
        hub = step.executor is Executor.HUB
        return [le for le in leases if le.hub == hub and le.epoch not in taken]

    def inside(lease: StepLease, opened: datetime, end: datetime | None) -> bool:
        return lease.minted_at >= opened and (end is None or lease.minted_at < end)

    chosen: list[StepLease | None] = []
    for step in steps:
        taken = {le.epoch for le in chosen if le is not None}
        own = mine(step, taken)
        if step.exit_epoch is not None:
            fenced = next((le for le in own if le.epoch == step.exit_epoch), None)
        else:
            fenced = max(own, key=lambda le: le.epoch, default=None)
        chosen.append(fenced if fenced is not None and inside(fenced, step.start - LEASE_SLACK, step.end) else None)
    for i, step in enumerate(steps):
        if chosen[i] is None:
            taken = {le.epoch for le in chosen if le is not None}
            chosen[i] = max(
                (le for le in mine(step, taken) if inside(le, step.start, step.end)),
                key=lambda le: le.minted_at,
                default=None,
            )
    return chosen


def _in_step[F: (StepUsage, StepEscalation)](
    facts: Iterable[F], epoch: int | None, start: datetime, end: datetime | None
) -> list[F]:
    """The facts recorded inside a step: those under the fence it ran, by epoch — else, with no fence to go by, those
    recorded within ``[start, end)``."""
    if epoch is not None:
        return [f for f in facts if f.epoch == epoch]
    return [f for f in facts if start <= f.recorded_at and (end is None or f.recorded_at < end)]


def _step_usage(
    chunk: ChunkTimelineFacts, node_id: str, epoch: int | None, start: datetime, end: datetime | None
) -> list[StepUsage]:
    """The invocations inside a step, at its node (:func:`_in_step`)."""
    return _in_step((u for u in chunk.usage if u.node_id == node_id), epoch, start, end)


def _step_escalations(
    chunk: ChunkTimelineFacts, epoch: int | None, start: datetime, end: datetime | None
) -> list[StepEscalation]:
    """The escalations inside a step, oldest first (:func:`_in_step`)."""
    return sorted(_in_step(chunk.escalations, epoch, start, end), key=lambda e: e.recorded_at)


class _StoredArtifact(Protocol):
    @property
    def artifact_id(self) -> str: ...
    @property
    def store_key(self) -> str: ...
    @property
    def node_id(self) -> str: ...
    @property
    def epoch(self) -> int: ...


def step_artifact(stored: _StoredArtifact) -> StepArtifact:
    """A stored artifact as a step attributes it — recorded at the instant its id was minted."""
    artifact_id = Id.parse(stored.artifact_id)
    return StepArtifact(
        key=stored.store_key,
        node_id=stored.node_id,
        epoch=stored.epoch,
        recorded_at=artifact_id.minted_at if artifact_id is not None else None,
    )


def step_artifacts(
    artifacts: Iterable[StepArtifact], node_id: str, epoch: int | None, start: datetime, end: datetime | None
) -> list[StepArtifact]:
    """The artifacts a step's node recorded inside it: under the fence it ran, by epoch — else, with no fence to go
    by, within ``[start, end]``."""
    mine = (a for a in artifacts if a.node_id == node_id)
    if epoch is not None:
        return [a for a in mine if a.epoch == epoch]
    return [
        a
        for a in mine
        if a.recorded_at is not None and start <= a.recorded_at and (end is None or a.recorded_at <= end)
    ]


def _segments(span: RouteSpan, chunk: ChunkTimelineFacts, facts: EnvTimelineFacts, now: datetime) -> list[EnvSegment]:
    tenure_end = _tenure_end(span, chunk)
    route_end = tenure_end or now
    runner = facts.roster.runners.get(span.runner_id)
    steps = _steps(chunk, facts.graphs, span.created_at)
    held: list[tuple[StepTransition, str, StepTransition | None, StepWindow]] = []
    for i, step in enumerate(steps):
        node_id = step.to_node_id
        if node_id is None or node_id == RESERVED_TERMINAL:
            continue
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        start = max(step.recorded_at, span.created_at)
        end = min(nxt.recorded_at, route_end) if nxt is not None else route_end
        running = nxt is None and tenure_end is None
        if end <= start and not running:
            continue
        executor = _node_executor(facts.graphs.get(step.graph_id), node_id)
        window = StepWindow(start, None if running else end, nxt.epoch if nxt is not None else None, executor)
        held.append((step, node_id, nxt, window))
    open_escalation = chunk.open_escalation()
    out: list[EnvSegment] = []
    for (step, node_id, nxt, window), lease in zip(held, step_leases([w for *_, w in held], chunk.leases), strict=True):
        graph = facts.graphs.get(step.graph_id)
        epoch = lease.epoch if lease is not None else None
        exit_choice = nxt.choice_name if nxt is not None else None
        happy = _happy_choice(graph, node_id)
        escalations = _step_escalations(chunk, epoch, window.start, window.end)
        usage = _step_usage(chunk, node_id, epoch, window.start, window.end)
        out.append(
            EnvSegment(
                chunk_id=chunk.chunk_id,
                title=chunk.title,
                project_id=chunk.project_id,
                runner_id=span.runner_id,
                runner_name=runner.name if runner is not None else span.runner_id,
                graph_id=step.graph_id,
                graph_name=graph.name if graph is not None else None,
                node_id=node_id,
                node_name=_node_name(graph, node_id),
                started_at=window.start,
                ended_at=window.end,
                exit_choice=exit_choice,
                failed=bool(escalations) or (exit_choice is not None and happy is not None and exit_choice != happy),
                escalation=escalations[-1].cause if escalations else None,
                escalation_open=bool(escalations) and escalations[-1] == open_escalation,
                cost_usd=round(
                    sum((u.cost_usd if u.cost_usd is not None else u.estimated_cost_usd or 0.0) for u in usage), 4
                ),
                cost_estimated=UsageTotal.of(usage).billed_partial,
                executor=window.executor,
                epoch=epoch,
                lease_id=lease.lease_id if lease is not None else None,
            )
        )
    return out


def _env_key(span: RouteSpan, roster: Roster, environment_id: str) -> EnvKey:
    runner = roster.runners.get(span.runner_id)
    return (runner.host_id if runner is not None else None, span.workspace_id, environment_id)


def env_timeline(facts: EnvTimelineFacts, *, since: datetime, until: datetime, now: datetime) -> EnvTimeline:
    """Every node step the routed chunks spent in an environment within ``[since, until)``, grouped host → workspace
    → environment. An environment appears only while it has a step in the window, so one retired since still shows
    the work it held."""
    lanes: dict[EnvKey, list[EnvSegment]] = defaultdict(list)
    for span in facts.routes:
        chunk = facts.chunks.get(span.chunk_id)
        if chunk is None:
            continue
        segments = [
            s
            for s in _segments(span, chunk, facts, now)
            if s.started_at < until and (s.ended_at is None or s.ended_at > since)
        ]
        for env in span.environment_ids:
            lanes[_env_key(span, facts.roster, env)].extend(segments)
    by_host: dict[str | None, dict[str, list[EnvLane]]] = defaultdict(lambda: defaultdict(list))
    for (host_id, workspace_id, env), segments in lanes.items():
        if segments:
            by_host[host_id][workspace_id].append(EnvLane(env, tuple(sorted(segments, key=lambda s: s.started_at))))
    runners = facts.roster.runners.values()
    hosts = tuple(
        HostLanes(
            host_id=host_id,
            host_name=facts.roster.hosts.get(host_id) if host_id else None,
            runner_names=tuple(sorted(r.name for r in runners if r.host_id == host_id)),
            workspaces=tuple(
                WorkspaceLanes(ws, tuple(sorted(envs, key=lambda e: e.environment_id)))
                for ws, envs in sorted(workspaces.items())
            ),
        )
        for host_id, workspaces in sorted(by_host.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
    )
    return EnvTimeline(since=since, until=until, generated_at=now, hosts=hosts)


def newest_per_environment(spans: Iterable[RouteSpan], roster: Roster) -> dict[EnvKey, RouteSpan]:
    """The route that last held each environment."""
    newest: dict[EnvKey, RouteSpan] = {}
    for span in spans:
        for env in span.environment_ids:
            key = _env_key(span, roster, env)
            held = newest.get(key)
            if held is None or span.created_at > held.created_at:
                newest[key] = span
    return newest


def held_environments(timeline: EnvTimeline) -> set[EnvKey]:
    """Every environment with a step on ``timeline``."""
    return {
        (h.host_id, ws.workspace_id, env.environment_id)
        for h in timeline.hosts
        for ws in h.workspaces
        for env in ws.environments
    }


def latest_only(timeline: EnvTimeline) -> EnvTimeline:
    """``timeline`` with every environment cut to its newest step."""
    return replace(
        timeline,
        hosts=tuple(
            replace(
                host,
                workspaces=tuple(
                    replace(ws, environments=tuple(replace(env, segments=env.segments[-1:]) for env in ws.environments))
                    for ws in host.workspaces
                ),
            )
            for host in timeline.hosts
        ),
    )


# --- One node step, in depth --------------------------------------------------------------------------------------


@domain_model
@dataclass(frozen=True)
class PlacedSegment:
    """A node step with the environment that held it."""

    workspace_id: str
    environment_id: str
    host_id: str | None
    host_name: str | None
    segment: EnvSegment


@domain_model
@dataclass(frozen=True)
class StepDetail:
    """One node step: where it ran, the invocations and escalations inside it, their summed ``spend`` (``None`` with
    no invocation), the keys of the artifacts its node recorded inside it, and the chunk's whole path across the
    environments around it (``journey``, oldest first)."""

    step: PlacedSegment
    invocations: tuple[StepUsage, ...]
    escalations: tuple[StepEscalation, ...]
    journey: tuple[PlacedSegment, ...]
    generated_at: datetime
    spend: UsageTotal | None = None
    artifact_keys: tuple[str, ...] = ()


class StepNotFound(LookupError):
    """The chunk has no node step by the lease or start instant asked for, or no step at all."""


#: How close a step's start must be to the instant asked for to be that step — a wire instant carries milliseconds.
_START_TOLERANCE = timedelta(milliseconds=1)


def _journey(facts: EnvTimelineFacts, chunk: ChunkTimelineFacts, now: datetime) -> list[PlacedSegment]:
    placed: list[PlacedSegment] = []
    for span in facts.routes:
        if span.chunk_id != chunk.chunk_id or not span.environment_ids:
            continue
        host_id, workspace_id, environment_id = _env_key(span, facts.roster, span.environment_ids[0])
        placed.extend(
            PlacedSegment(
                workspace_id=workspace_id,
                environment_id=environment_id,
                host_id=host_id,
                host_name=facts.roster.hosts.get(host_id) if host_id else None,
                segment=seg,
            )
            for seg in _segments(span, chunk, facts, now)
        )
    return sorted(placed, key=lambda p: p.segment.started_at)


def step_detail(
    facts: EnvTimelineFacts,
    *,
    chunk_id: str,
    now: datetime,
    lease_id: str | None = None,
    started_at: datetime | None = None,
    artifacts: Sequence[StepArtifact] = (),
) -> StepDetail:
    """A node step of ``chunk_id`` in depth: the one ``lease_id`` ran, else the one that starts at ``started_at`` (to
    the millisecond), else the chunk's newest — with those of the chunk's ``artifacts`` its node recorded inside it.
    Raises :class:`StepNotFound` when there is none."""
    chunk = facts.chunks.get(chunk_id)
    if chunk is None:
        raise StepNotFound(chunk_id)
    journey = _journey(facts, chunk, now)
    if lease_id is not None:
        match = next((p for p in journey if p.segment.lease_id == lease_id), None)
    elif started_at is not None:
        match = next((p for p in journey if abs(p.segment.started_at - started_at) < _START_TOLERANCE), None)
    else:
        match = journey[-1] if journey else None
    if match is None:
        raise StepNotFound(chunk_id)
    seg = match.segment
    invocations = sorted(
        _step_usage(chunk, seg.node_id, seg.epoch, seg.started_at, seg.ended_at), key=lambda u: u.recorded_at
    )
    return StepDetail(
        step=match,
        invocations=tuple(invocations),
        escalations=tuple(_step_escalations(chunk, seg.epoch, seg.started_at, seg.ended_at)),
        journey=tuple(journey),
        generated_at=now,
        spend=UsageTotal.of(invocations) if invocations else None,
        artifact_keys=tuple(
            a.key for a in step_artifacts(artifacts, seg.node_id, seg.epoch, seg.started_at, seg.ended_at)
        ),
    )


class IReadEnvTimelineFacts(Protocol):
    """The timeline's reads, each in one read transaction of its own."""

    def routes(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        chunk_id: str | None = None,
        project: str | None = None,
    ) -> tuple[list[RouteCreated], list[RouteReleased]]:
        """Every route created before ``until`` whose tenure may reach past ``since`` — not released, nor its chunk
        ended, between its creation and then — with its environments, and every release of those routes' chunks.
        ``since=None`` reaches back to the first route and ``until=None`` forward to the newest; ``chunk_id`` and
        ``project`` narrow to one chunk's routes, or one project's chunks'."""
        ...

    def chunk_facts(self, chunk_ids: Sequence[str]) -> dict[str, ChunkTimelineFacts]:
        """Each listed chunk's transitions and cross-graph moves, mints — runner leases and the hub's own, told apart
        by the epoch's owner —,
        escalations, usage, endings, requeues, restarts, project, graph and work-item title; an id naming no chunk is
        dropped."""
        ...

    def roster(self) -> Roster:
        """Every registered runner's name and host, and every host's name."""
        ...


class EnvTimelineReading:
    """Reads the facts a timeline or a step needs and derives it, taking the clock once per read."""

    def __init__(
        self,
        *,
        facts: IReadEnvTimelineFacts,
        graphs: IReadGraphRepository,
        artifacts: IReadChunkArtifactsRepository,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._graphs = graphs
        self._artifacts = artifacts
        self._clock = clock

    def _timeline_facts(
        self, spans: Sequence[RouteSpan], chunks: Mapping[str, ChunkTimelineFacts], roster: Roster
    ) -> EnvTimelineFacts:
        graph_ids = sorted(
            {g for c in chunks.values() for t in c.transitions for g in (t.graph_id, t.from_graph_id) if g}
            | {g for c in chunks.values() for m in c.migrations for g in (m.from_graph_id, m.to_graph_id)}
            | {c.graph_id for c in chunks.values() if c.graph_id}
        )
        return EnvTimelineFacts(
            routes=tuple(spans), chunks=chunks, roster=roster, graphs=self._graphs.get_many(graph_ids)
        )

    def read(self, *, since: datetime, until: datetime | None, project: str | None = None) -> EnvTimeline:
        """Every node step a routed chunk spent in an environment within ``[since, until)`` — ``until`` defaults to
        now, and ``since`` is narrowed by :func:`window_start`."""
        now = self._clock.now()
        end = until or now
        start = window_start(since, end)
        created, released = self._facts.routes(since=start, until=end, project=project)
        spans = overlapping(route_spans(created, released), start, end)
        chunks = self._facts.chunk_facts(sorted({s.chunk_id for s in spans}))
        # The whole roster, not the window's runners: a host names every runner on it.
        facts = self._timeline_facts(spans, chunks, self._facts.roster())
        return env_timeline(facts, since=start, until=end, now=now)

    def read_latest(self, *, since: datetime, project: str | None = None) -> EnvTimeline:
        """Each environment's newest step since ``since``, narrowed by :func:`window_start` — what it holds now, or
        what it last held. Only the route that last held each environment is read in depth; an environment its newest
        route left bare — re-routed before it moved — looks one route further back, :data:`LATEST_ROUNDS` times at
        most."""
        now = self._clock.now()
        start = window_start(since, now)
        created, released = self._facts.routes(since=start, until=now, project=project)
        roster = self._facts.roster()
        remaining = overlapping(route_spans(created, released), start, now)
        chosen: list[RouteSpan] = []
        chunks: dict[str, ChunkTimelineFacts] = {}
        wanted: set[EnvKey] | None = None
        timeline = EnvTimeline(since=start, until=now, generated_at=now)
        for _ in range(LATEST_ROUNDS):
            newest = newest_per_environment(remaining, roster)
            if wanted is not None:
                newest = {k: v for k, v in newest.items() if k in wanted}
            if not newest:
                break
            picked = {id(s): s for s in newest.values()}
            chosen.extend(picked.values())
            remaining = [s for s in remaining if id(s) not in picked]
            unread = sorted({s.chunk_id for s in picked.values()} - chunks.keys())
            chunks.update(self._facts.chunk_facts(unread))
            timeline = env_timeline(self._timeline_facts(chosen, chunks, roster), since=start, until=now, now=now)
            wanted = set(newest) - held_environments(timeline)
            if not wanted:
                break
        return latest_only(timeline)

    def step(self, *, chunk_id: str, lease_id: str | None = None, started_at: datetime | None = None) -> StepDetail:
        """One node step of ``chunk_id`` in depth, chosen as :func:`step_detail` chooses it, over every route the
        chunk ever took."""
        now = self._clock.now()
        created, released = self._facts.routes(since=None, until=None, chunk_id=chunk_id)
        chunks = self._facts.chunk_facts([chunk_id])
        facts = self._timeline_facts(route_spans(created, released), chunks, self._facts.roster())
        stored = self._artifacts.load_artifacts_for([chunk_id] if chunk_id in chunks else [])
        artifacts = [step_artifact(a) for a in stored.get(chunk_id, [])]
        return step_detail(
            facts, chunk_id=chunk_id, lease_id=lease_id, started_at=started_at, artifacts=artifacts, now=now
        )
