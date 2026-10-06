"""The input bundle for lease assembly — one closed lease's rows from every table its spans are built from.

Declares only the columns the runner span contract reads, never a content column: no question text, check command or
output, process id, path, git declaration or stdout ever enters, so none can leave. Hydrating it is the store's job.
Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/spans.md`` §What never leaves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.runner.transcripts.invocation_boundaries import InvocationBoundaryKind


@domain_model
@dataclass(frozen=True)
class LeaseGrantFact:
    """A ``leases`` row."""

    lease_id: str
    chunk_id: str
    epoch: int
    created_at: datetime


@domain_model
@dataclass(frozen=True)
class RunnerFact:
    """The ``runner_identity`` row: the id and name of the runner's latest registration, which every lease's
    spans carry — a lease minted before that registration included."""

    runner_id: str
    runner_name: str


@domain_model
@dataclass(frozen=True)
class LeaseContextFact:
    """A ``lease_context`` row; ``None`` declares unknown."""

    graph_id: str
    node_id: str
    node_name: str
    graph_name: str | None = None
    #: Each work ref as its source-native token (``acme#42``).
    work_refs: tuple[str, ...] = ()
    session_name: str | None = None
    resolved_model: str | None = None
    resolved_effort: str | None = None


@domain_model
@dataclass(frozen=True)
class LeaseClosureFact:
    """A ``lease_closures`` row; ``reason`` verbatim, mint reasons included."""

    reason: str
    closed_at: datetime


@domain_model
@dataclass(frozen=True)
class SpawnFact:
    """A ``lease_spawns`` row — identity only, never its process facts."""

    id: int
    spawned_at: datetime
    harness_id: str | None = None
    harness_version: str | None = None
    session_id: str | None = None
    identified_at: datetime | None = None


@domain_model
@dataclass(frozen=True)
class BoundaryFact:
    """An ``invocation_boundaries`` row — never its transcript position."""

    id: int
    generation: int
    kind: InvocationBoundaryKind
    opened_at: datetime
    closed_at: datetime | None = None


@domain_model
@dataclass(frozen=True)
class TokenUsageFact:
    """A ``usage_facts`` row."""

    id: int
    generation: int
    kind: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    recorded_at: datetime
    cost_usd: float | None = None
    estimated_cost_usd: float | None = None
    harness_id: str | None = None
    harness_version: str | None = None


@domain_model
@dataclass(frozen=True)
class SessionEndFact:
    id: int
    ended_at: datetime


@domain_model
@dataclass(frozen=True)
class ContextSampleFact:
    """A ``context_samples`` row; ``context_tokens`` ``None`` is an unmeasurable sample."""

    id: int
    sampled_at: datetime
    context_tokens: int | None = None


@domain_model
@dataclass(frozen=True)
class ParkFact:
    """A ``park_facts`` row — the question's id, never its text."""

    id: int
    question_id: str
    parked_at: datetime


@domain_model
@dataclass(frozen=True)
class ParkResumeFact:
    id: int
    question_id: str
    resumed_at: datetime


@domain_model
@dataclass(frozen=True)
class PauseParkFact:
    id: int
    parked_at: datetime


@domain_model
@dataclass(frozen=True)
class PauseResumeFact:
    """A ``pause_park_resumes`` row."""

    id: int
    resumed_at: datetime


@domain_model
@dataclass(frozen=True)
class OverloadFact:
    """An ``overload_facts`` row; ``resume_after`` ``None`` is a recorded fall-through."""

    id: int
    generation: int
    streak_ordinal: int
    observed_at: datetime
    resume_after: datetime | None = None


@domain_model
@dataclass(frozen=True)
class TakeoverFact:
    """A ``takeovers`` row — never its working directory or session."""

    takeover_id: str
    opened_at: datetime


@domain_model
@dataclass(frozen=True)
class TakeoverEndFact:
    id: int
    takeover_id: str
    ended_at: datetime


@domain_model
@dataclass(frozen=True)
class NudgeFact:
    id: int
    epoch: int
    nudged_at: datetime


@domain_model
@dataclass(frozen=True)
class CheckResultFact:
    """A ``check_results`` row — the outcome only, never the command or its output."""

    id: int
    epoch: int
    passed: bool


@domain_model
@dataclass(frozen=True)
class ChecksRanFact:
    id: int
    epoch: int
    ran_at: datetime


@domain_model
@dataclass(frozen=True)
class LeaseTraceFacts:
    """One closed lease's facts; every row tuple holds only that lease's rows."""

    lease: LeaseGrantFact
    context: LeaseContextFact
    closure: LeaseClosureFact
    runner: RunnerFact
    spawns: tuple[SpawnFact, ...] = ()
    boundaries: tuple[BoundaryFact, ...] = ()
    usage: tuple[TokenUsageFact, ...] = ()
    session_ends: tuple[SessionEndFact, ...] = ()
    context_samples: tuple[ContextSampleFact, ...] = ()
    parks: tuple[ParkFact, ...] = ()
    park_resumes: tuple[ParkResumeFact, ...] = ()
    pause_parks: tuple[PauseParkFact, ...] = ()
    pause_resumes: tuple[PauseResumeFact, ...] = ()
    overloads: tuple[OverloadFact, ...] = ()
    takeovers: tuple[TakeoverFact, ...] = ()
    takeover_ends: tuple[TakeoverEndFact, ...] = ()
    nudges: tuple[NudgeFact, ...] = ()
    check_results: tuple[CheckResultFact, ...] = ()
    checks_ran: tuple[ChecksRanFact, ...] = ()
