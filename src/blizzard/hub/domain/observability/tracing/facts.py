"""The input bundle for step identification — the tracing-shaped slice of a chunk's facts.

Declares only the columns the span contract reads (``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md``),
so the aggregate behind every hot-path read stays untouched; hydrating this bundle is the store's job."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.roles import dto
from blizzard.hub.domain.chunk.model import UsageFact
from blizzard.hub.domain.graph.model import Graph


@dto
@dataclass(frozen=True)
class TracedLease:
    """A ``lease_facts`` row. Deliberately carries no runner id: ownership is read from epoch owners."""

    epoch: int
    minted_at: datetime


@dto
@dataclass(frozen=True)
class TracedEpochOwner:
    """An ``epoch_owners`` row — ``runner_id`` ``None`` meaning the hub."""

    epoch: int
    runner_id: str | None
    recorded_at: datetime


@dto
@dataclass(frozen=True)
class TracedTransition:
    epoch: int
    recorded_at: datetime
    graph_id: str
    to_node_id: str
    from_node_id: str | None = None
    decision_id: str | None = None
    choice_name: str | None = None


@dto
@dataclass(frozen=True)
class TracedMigration:
    epoch: int
    recorded_at: datetime
    from_graph_id: str
    to_graph_id: str
    from_node_id: str | None = None
    landed_node_id: str | None = None
    source: MigrationSource | None = None
    decision_id: str | None = None
    choice_name: str | None = None


@dto
@dataclass(frozen=True)
class TracedRestart:
    epoch: int
    recorded_at: datetime
    #: The graph ``to_node_id`` belongs to.
    graph_id: str
    to_node_id: str
    from_graph_id: str | None = None
    from_node_id: str | None = None
    decision_id: str | None = None


@dto
@dataclass(frozen=True)
class TracedEscalation:
    epoch: int
    recorded_at: datetime
    decision_id: str | None = None


@dto
@dataclass(frozen=True)
class TracedDecision:
    """A ``decisions`` row; ``imposed_by_runner_id`` is non-null exactly for a runner gate."""

    decision_id: str
    node_id: str
    epoch: int
    submitted_at: datetime
    imposed_by_runner_id: str | None = None


@dto
@dataclass(frozen=True)
class TracedDecisionResolution:
    """The moment a person decided — not when the holding runner picked it up."""

    decision_id: str
    resolved_at: datetime
    #: The resolved choice's name — never its description.
    choice: str | None = None


@dto
@dataclass(frozen=True)
class TracedRequeue:
    requeued_at: datetime


@dto
@dataclass(frozen=True)
class TracedRouteRelease:
    released_at: datetime


@dto
@dataclass(frozen=True)
class TracedChunkStop:
    recorded_at: datetime


@dto
@dataclass(frozen=True)
class TracedChunkCompletion:
    recorded_at: datetime


@dto
@dataclass(frozen=True)
class TracedQuestion:
    """A ``questions`` row with its answer's instant; the question and answer text never ride."""

    question_id: str
    epoch: int
    asked_at: datetime
    answered_at: datetime | None = None


@dto
@dataclass(frozen=True)
class TracedPause:
    """A pause fact: ``paused`` sets it, ``paused=False`` lifts it. Carries no ``set_by``."""

    id: str
    paused: bool
    set_at: datetime


@dto
@dataclass(frozen=True)
class TracedHubExecSlot:
    """A ``hub_exec_slot`` row — keyed by node, with no epoch."""

    slot_id: str
    node_id: str
    acquired_at: datetime
    released_at: datetime | None = None


@dto
@dataclass(frozen=True)
class TracedHubPoll:
    """A ``hub_node_poll`` row, recorded at the epoch the chunk arrived at the hub node with."""

    id: str
    node_id: str
    epoch: int
    polled_at: datetime


@dto
@dataclass(frozen=True)
class TracedBounce:
    """A ``chunk_bounces`` row, without its envelope."""

    epoch: int
    cause: str
    recorded_at: datetime


@dto
@dataclass(frozen=True)
class TracedRouteCreation:
    created_at: datetime


@dto
@dataclass(frozen=True)
class TracedPromotion:
    promoted_at: datetime


@dto
@dataclass(frozen=True)
class TracedPrerequisiteMet:
    """The instant a prerequisite completed, resolved by the hydrator from the prerequisite's own facts."""

    met_at: datetime


@dto
@dataclass(frozen=True)
class StepFacts:
    """Everything step identification reads about one chunk.

    ``graphs`` holds the graphs the facts reference, keyed by ``graph_id``. ``pin_graph_id`` is the
    chunk's current pin, read only for a chunk with no movement fact at all."""

    chunk_id: str
    graphs: dict[str, Graph] = field(default_factory=dict)
    pin_graph_id: str | None = None
    #: The ingest instant, where the lifetime root starts.
    minted_at: datetime | None = None
    lease_facts: tuple[TracedLease, ...] = ()
    epoch_owners: tuple[TracedEpochOwner, ...] = ()
    transitions: tuple[TracedTransition, ...] = ()
    migrations: tuple[TracedMigration, ...] = ()
    restarts: tuple[TracedRestart, ...] = ()
    escalations: tuple[TracedEscalation, ...] = ()
    decisions: tuple[TracedDecision, ...] = ()
    decision_resolutions: tuple[TracedDecisionResolution, ...] = ()
    requeues: tuple[TracedRequeue, ...] = ()
    route_released: tuple[TracedRouteRelease, ...] = ()
    chunk_stopped: tuple[TracedChunkStop, ...] = ()
    chunk_completed: tuple[TracedChunkCompletion, ...] = ()
    questions: tuple[TracedQuestion, ...] = ()
    pauses: tuple[TracedPause, ...] = ()
    hub_exec_slots: tuple[TracedHubExecSlot, ...] = ()
    hub_polls: tuple[TracedHubPoll, ...] = ()
    bounces: tuple[TracedBounce, ...] = ()
    routes_created: tuple[TracedRouteCreation, ...] = ()
    promotions: tuple[TracedPromotion, ...] = ()
    prerequisites_met: tuple[TracedPrerequisiteMet, ...] = ()
    usage: tuple[UsageFact, ...] = ()
    #: Source-native work-ref tokens (``acme#42``), rendered by the hydrator's configured binding.
    work_refs: tuple[str, ...] = ()
    #: The distinct work sources behind ``work_refs``, in ref order — read from the rows, never parsed from labels.
    work_sources: tuple[str, ...] = ()
