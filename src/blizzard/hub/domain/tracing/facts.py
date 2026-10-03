"""The input bundle for step identification — the tracing-shaped slice of a chunk's facts.

Declares only the columns the span contract reads (``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md``),
so the aggregate behind every hot-path read stays untouched; hydrating this bundle is the store's job."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import MigrationSource, UsageFact


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
    choice_name: str | None = None


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
    choice_name: str | None = None


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
    #: The resolved choice's name — never its description.
    choice: str | None = None


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
class QuestionRecord:
    """A ``questions`` row with its answer's instant; the question and answer text never ride."""

    question_id: str
    epoch: int
    asked_at: datetime
    answered_at: datetime | None = None


@dataclass(frozen=True)
class PauseRecord:
    """A pause fact: ``paused`` sets it, ``paused=False`` lifts it. Carries no ``set_by``."""

    id: str
    paused: bool
    set_at: datetime


@dataclass(frozen=True)
class HubExecSlotRecord:
    """A ``hub_exec_slot`` row — keyed by node, with no epoch."""

    slot_id: str
    node_id: str
    acquired_at: datetime
    released_at: datetime | None = None


@dataclass(frozen=True)
class HubPollRecord:
    """A ``hub_node_poll`` row, recorded at the epoch the chunk arrived at the hub node with."""

    id: str
    node_id: str
    epoch: int
    polled_at: datetime


@dataclass(frozen=True)
class BounceRecord:
    """A ``chunk_bounces`` row, without its envelope."""

    epoch: int
    cause: str
    recorded_at: datetime


@dataclass(frozen=True)
class RouteCreatedRecord:
    created_at: datetime


@dataclass(frozen=True)
class PromotionRecord:
    promoted_at: datetime


@dataclass(frozen=True)
class PrerequisiteMetRecord:
    """The instant a prerequisite completed, resolved by the hydrator from the prerequisite's own facts."""

    met_at: datetime


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
    questions: tuple[QuestionRecord, ...] = ()
    pauses: tuple[PauseRecord, ...] = ()
    hub_exec_slots: tuple[HubExecSlotRecord, ...] = ()
    hub_polls: tuple[HubPollRecord, ...] = ()
    bounces: tuple[BounceRecord, ...] = ()
    routes_created: tuple[RouteCreatedRecord, ...] = ()
    promotions: tuple[PromotionRecord, ...] = ()
    prerequisites_met: tuple[PrerequisiteMetRecord, ...] = ()
    usage: tuple[UsageFact, ...] = ()
    #: Source-native work-ref tokens (``acme#42``), rendered by the hydrator's configured binding.
    work_refs: tuple[str, ...] = ()
