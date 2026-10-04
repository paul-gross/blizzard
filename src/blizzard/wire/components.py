"""The schema components each daemon's OpenAPI spec carries beyond what its routes reach.

A shape a client parses off a non-route channel — an SSE frame, an artifact body — and a
vocabulary a client iterates are registered here, so the generated client types them from
the one Python definition. Models register in serialization mode; enums and named
``Literal`` vocabularies register by component name."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.findings import FindingExit, FindingFactKind, FindingSeverity, FindingSource, FindingState
from blizzard.foundation.ids import IdPrefix
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.wire.finding import (
    AddFindingOp,
    FindingCandidate,
    FindingDelta,
    FindingSurvey,
    GoneFindingOp,
    ObservedFindingOp,
)
from blizzard.wire.sse import (
    ChunkChangeCause,
    ChunkChangedPayload,
    DecisionOpenedPayload,
    DecisionResolvedPayload,
    EventLoggedPayload,
    HubEventType,
    QuestionAnsweredPayload,
    QuestionAskedPayload,
    QueueChangedPayload,
    RunnerChangedPayload,
    RunnerChangeKind,
)
from blizzard.wire.sse_runner import (
    AskChangeCause,
    AskChangedPayload,
    EnvironmentChangeCause,
    EnvironmentChangedPayload,
    EscalationChangeCause,
    EscalationChangedPayload,
    FactChangedPayload,
    LeaseChangeCause,
    LeaseChangedPayload,
    RunnerEventType,
    TakeoverChangeCause,
    TakeoverChangedPayload,
)


class SchemaComponents:
    """One spec's extra components: payload models, plus vocabularies keyed by component name."""

    def __init__(self, models: tuple[type[BaseModel], ...], enums: dict[str, object]) -> None:
        self.models = models
        self.enums = enums


HUB_SCHEMA_COMPONENTS = SchemaComponents(
    models=(
        FindingDelta,
        AddFindingOp,
        ObservedFindingOp,
        GoneFindingOp,
        FindingSurvey,
        FindingCandidate,
        ChunkChangedPayload,
        QuestionAskedPayload,
        QuestionAnsweredPayload,
        DecisionOpenedPayload,
        DecisionResolvedPayload,
        QueueChangedPayload,
        RunnerChangedPayload,
        EventLoggedPayload,
    ),
    enums={
        "HubEventType": HubEventType,
        "ChunkChangeCause": ChunkChangeCause,
        "RunnerChangeKind": RunnerChangeKind,
        "IdPrefix": IdPrefix,
        "FindingState": FindingState,
        "FindingExit": FindingExit,
        "FindingFactKind": FindingFactKind,
        "FindingSeverity": FindingSeverity,
        "FindingSource": FindingSource,
        "EscalationCause": EscalationCause,
    },
)

RUNNER_SCHEMA_COMPONENTS = SchemaComponents(
    models=(
        LeaseChangedPayload,
        AskChangedPayload,
        EscalationChangedPayload,
        TakeoverChangedPayload,
        EnvironmentChangedPayload,
        FactChangedPayload,
    ),
    enums={
        "RunnerEventType": RunnerEventType,
        "LeaseChangeCause": LeaseChangeCause,
        "AskChangeCause": AskChangeCause,
        "EscalationChangeCause": EscalationChangeCause,
        "TakeoverChangeCause": TakeoverChangeCause,
        "EnvironmentChangeCause": EnvironmentChangeCause,
        "LeaseClosureReason": LeaseClosureReason,
    },
)
