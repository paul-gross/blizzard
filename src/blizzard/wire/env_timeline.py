"""The environment-timeline wire views: every node step a chunk spent in a runner environment, host → workspace →
environment."""

from __future__ import annotations

from typing import Annotated

from blizzard.foundation.node_steps import Executor
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.chunk import ChunkUsageTotalView
from blizzard.wire.vocabulary import OPEN


class EnvTimelineSegmentView(WireModel):
    """One node step one chunk spent in one environment. ``ended_at`` is ``None`` while the chunk is still in it;
    ``failed`` holds when the step left by a choice other than its node's first-declared one, or escalated —
    ``escalation`` then names the cause, and ``escalation_open`` holds while that escalation still waits on a human:
    nothing since — a fresh session, a requeue, a restart or the chunk's end — superseded it. ``cost_estimated``
    holds when some of ``cost_usd`` is a runner-side estimate rather than a billed amount."""

    chunk_id: str
    title: str | None = None
    project_id: str | None = None
    runner_id: str
    runner_name: str
    graph_id: str | None = None
    graph_name: str | None = None
    node_id: str
    node_name: str
    started_at: str
    ended_at: str | None = None
    exit_choice: str | None = None
    failed: bool
    escalation: str | None = None
    escalation_open: bool = False
    cost_usd: float
    cost_estimated: bool
    #: Where the step's node runs — on the hub or on a runner; absent for a node on no graph the hub still knows.
    executor: Annotated[Executor | Unrecognized, OPEN] | None = None
    #: The fence the step ran under — a runner's, or the hub's own for a hub step; absent for a runner step whose
    #: session the hub never heard minted, so the hub holds no transcript for it.
    epoch: int | None = None
    #: The runner lease — the session — that ran the step: absent for every hub step, for a runner step whose session
    #: the hub never heard minted (``epoch`` absent too), and for a runner's session that took the chunk over on a
    #: forced entry (``epoch`` set — its transcript is still read by it). Whether the step ran on the hub is
    #: ``executor``'s to say.
    lease_id: str | None = None


class EnvTimelineBlockView(WireModel):
    """One node step on an environment's track, its chunk, runner and node named by id into the view's tables.
    Every field reads as on ``EnvTimelineSegmentView``."""

    chunk_id: str
    runner_id: str
    node_id: str
    started_at: str
    ended_at: str | None = None
    exit_choice: str | None = None
    failed: bool
    escalation: str | None = None
    escalation_open: bool = False
    cost_usd: float
    cost_estimated: bool
    #: Where the step's node runs — on the hub or on a runner; absent for a node on no graph the hub still knows.
    executor: Annotated[Executor | Unrecognized, OPEN] | None = None
    #: The fence the step ran under — a runner's, or the hub's own for a hub step; absent for a runner step whose
    #: session the hub never heard minted, so the hub holds no transcript for it.
    epoch: int | None = None
    #: The runner lease — the session — that ran the step: absent for every hub step, for a runner step whose session
    #: the hub never heard minted (``epoch`` absent too), and for a runner's session that took the chunk over on a
    #: forced entry (``epoch`` set — its transcript is still read by it). Whether the step ran on the hub is
    #: ``executor``'s to say.
    lease_id: str | None = None


class EnvTimelineEnvironmentView(WireModel):
    environment_id: str
    blocks: list[EnvTimelineBlockView]


class EnvTimelineWorkspaceView(WireModel):
    workspace_id: str
    environments: list[EnvTimelineEnvironmentView]


class EnvTimelineHostView(WireModel):
    """``host_id`` and ``host_name`` are ``None`` for the runners on no host record."""

    host_id: str | None = None
    host_name: str | None = None
    runner_names: list[str]
    workspaces: list[EnvTimelineWorkspaceView]


class EnvTimelineChunkView(WireModel):
    title: str | None = None
    project_id: str | None = None


class EnvTimelineNodeView(WireModel):
    graph_name: str | None = None
    node_name: str


class EnvTimelineView(WireModel):
    """The window ``[since, until)`` the steps touch, and the hub's own now when it answered. Each block's chunk,
    runner and node are named once, in ``chunks``, ``runners`` (id → name) and ``nodes``."""

    since: str
    until: str
    generated_at: str
    hosts: list[EnvTimelineHostView]
    chunks: dict[str, EnvTimelineChunkView]
    runners: dict[str, str]
    nodes: dict[str, EnvTimelineNodeView]


class EnvTimelinePlacedStepView(WireModel):
    """A node step with the environment, workspace and host that held it."""

    workspace_id: str
    environment_id: str
    host_id: str | None = None
    host_name: str | None = None
    segment: EnvTimelineSegmentView


class EnvTimelineInvocationView(WireModel):
    """One harness invocation's usage inside the step: ``kind`` is ``spawn`` (the step's session), ``resume`` (that
    session picked back up) or ``judge`` (the call that chose the step's exit)."""

    recorded_at: str
    kind: str
    model: str | None = None
    harness_id: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None = None
    estimated_cost_usd: float | None = None


class EnvTimelineStepEscalationView(WireModel):
    recorded_at: str
    cause: str | None = None
    detail: str | None = None


class EnvTimelineStepView(WireModel):
    """One node step in depth: the harness invocations and escalations inside it, and the chunk's whole path across
    environments (``journey``, oldest first). ``spend`` sums the invocations under the chunk total's own contract,
    ``cost_partial`` and ``billed_partial`` included — absent with no invocation. ``artifact_keys`` names, by their
    key on the chunk's artifact list, the artifacts the step's node recorded inside it."""

    step: EnvTimelinePlacedStepView
    invocations: list[EnvTimelineInvocationView]
    escalations: list[EnvTimelineStepEscalationView]
    journey: list[EnvTimelinePlacedStepView]
    generated_at: str
    spend: ChunkUsageTotalView | None = None
    artifact_keys: list[str] = []
