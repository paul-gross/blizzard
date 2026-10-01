"""The input bundle for step identification — the tracing-shaped slice of a chunk's facts.

Declares only the columns the span contract reads (``blizzard-product:/plans/tracing/fleet-spans/spec/spans.md``),
so the aggregate behind every hot-path read stays untouched; hydrating this bundle is the store's job."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import MigrationSource


@dataclass(frozen=True)
class LeaseRecord:
    """A ``lease_facts`` row. Deliberately carries no runner id: ownership is read from epoch owners."""

    epoch: int
    minted_at: datetime


@dataclass(frozen=True)
class EpochOwnerRecord:
    """An ``epoch_owners`` row — ``runner_id`` ``None`` meaning the hub."""

    epoch: int
    runner_id: str | None
    recorded_at: datetime


@dataclass(frozen=True)
class TransitionRecord:
    epoch: int
    recorded_at: datetime
    graph_id: str
    to_node_id: str
    from_node_id: str | None = None
    decision_id: str | None = None


@dataclass(frozen=True)
class MigrationRecord:
    epoch: int
    recorded_at: datetime
    from_graph_id: str
    to_graph_id: str
    from_node_id: str | None = None
    landed_node_id: str | None = None
    source: MigrationSource | None = None
    decision_id: str | None = None


@dataclass(frozen=True)
class RestartRecord:
    epoch: int
    recorded_at: datetime
    #: The graph ``to_node_id`` belongs to.
    graph_id: str
    to_node_id: str
    from_graph_id: str | None = None
    from_node_id: str | None = None
    decision_id: str | None = None


@dataclass(frozen=True)
class EscalationRecord:
    epoch: int
    recorded_at: datetime
    decision_id: str | None = None


@dataclass(frozen=True)
class DecisionRecord:
    """A ``decisions`` row; ``imposed_by_runner_id`` is non-null exactly for a runner gate."""

    decision_id: str
    node_id: str
    epoch: int
    submitted_at: datetime
    imposed_by_runner_id: str | None = None


@dataclass(frozen=True)
class DecisionResolutionRecord:
    """The moment a person decided — not when the holding runner picked it up."""

    decision_id: str
    resolved_at: datetime


@dataclass(frozen=True)
class RequeueRecord:
    requeued_at: datetime


@dataclass(frozen=True)
class RouteReleasedRecord:
    released_at: datetime


@dataclass(frozen=True)
class ChunkStoppedRecord:
    recorded_at: datetime


@dataclass(frozen=True)
class ChunkCompletedRecord:
    recorded_at: datetime


@dataclass(frozen=True)
class StepFacts:
    """Everything step identification reads about one chunk.

    ``graphs`` holds the graphs the facts reference, keyed by ``graph_id``. ``pin_graph_id`` is the
    chunk's current pin, read only for a chunk with no movement fact at all."""

    chunk_id: str
    graphs: dict[str, Graph] = field(default_factory=dict)
    pin_graph_id: str | None = None
    lease_facts: tuple[LeaseRecord, ...] = ()
    epoch_owners: tuple[EpochOwnerRecord, ...] = ()
    transitions: tuple[TransitionRecord, ...] = ()
    migrations: tuple[MigrationRecord, ...] = ()
    restarts: tuple[RestartRecord, ...] = ()
    escalations: tuple[EscalationRecord, ...] = ()
    decisions: tuple[DecisionRecord, ...] = ()
    decision_resolutions: tuple[DecisionResolutionRecord, ...] = ()
    requeues: tuple[RequeueRecord, ...] = ()
    route_released: tuple[RouteReleasedRecord, ...] = ()
    chunk_stopped: tuple[ChunkStoppedRecord, ...] = ()
    chunk_completed: tuple[ChunkCompletedRecord, ...] = ()
