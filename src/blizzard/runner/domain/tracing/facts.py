"""The input bundle for lease assembly — one closed lease's rows from every table its spans are built from.

Declares only the columns the runner span contract reads, never a content column: no question text, check command or
output, process id, path, git declaration or stdout ever enters, so none can leave. Hydrating it is the store's job.
Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/spans.md`` §What never leaves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.runner.domain.invocation_boundaries import InvocationBoundaryKind


@dataclass(frozen=True)
class LeaseRow:
    """A ``leases`` row."""

    lease_id: str
    chunk_id: str
    epoch: int
    runner_id: str
    created_at: datetime


@dataclass(frozen=True)
class LeaseContextRow:
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


@dataclass(frozen=True)
class LeaseClosureRow:
    """A ``lease_closures`` row; ``reason`` verbatim, mint reasons included."""

    reason: str
    closed_at: datetime


@dataclass(frozen=True)
class SpawnRow:
    """A ``lease_spawns`` row — identity only, never its process facts."""

    id: int
    spawned_at: datetime
    harness_id: str | None = None
    harness_version: str | None = None
    session_id: str | None = None
    identified_at: datetime | None = None


@dataclass(frozen=True)
class BoundaryRow:
    """An ``invocation_boundaries`` row — never its transcript position."""

    id: int
    generation: int
    kind: InvocationBoundaryKind
    opened_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class UsageRow:
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


@dataclass(frozen=True)
class SessionEndRow:
    id: int
    ended_at: datetime


@dataclass(frozen=True)
class ContextSampleRow:
    """A ``context_samples`` row; ``context_tokens`` ``None`` is an unmeasurable sample."""

    id: int
    sampled_at: datetime
    context_tokens: int | None = None


@dataclass(frozen=True)
class ParkRow:
    """A ``park_facts`` row — the question's id, never its text."""

    id: int
    question_id: str
    parked_at: datetime


@dataclass(frozen=True)
class ParkResumeRow:
    id: int
    question_id: str
    resumed_at: datetime


@dataclass(frozen=True)
class PauseParkRow:
    id: int
    parked_at: datetime


@dataclass(frozen=True)
class PauseResumeRow:
    """A ``pause_park_resumes`` row."""

    id: int
    resumed_at: datetime


@dataclass(frozen=True)
class OverloadRow:
    """An ``overload_facts`` row; ``resume_after`` ``None`` is a recorded fall-through."""

    id: int
    generation: int
    streak_ordinal: int
    observed_at: datetime
    resume_after: datetime | None = None


@dataclass(frozen=True)
class TakeoverRow:
    """A ``takeovers`` row — never its working directory or session."""

    takeover_id: str
    opened_at: datetime


@dataclass(frozen=True)
class TakeoverEndRow:
    id: int
    takeover_id: str
    ended_at: datetime


@dataclass(frozen=True)
class NudgeRow:
    id: int
    epoch: int
    nudged_at: datetime


@dataclass(frozen=True)
class CheckResultRow:
    """A ``check_results`` row — the outcome only, never the command or its output."""

    id: int
    epoch: int
    passed: bool


@dataclass(frozen=True)
class ChecksRanRow:
    id: int
    epoch: int
    ran_at: datetime


@dataclass(frozen=True)
class LeaseTraceFacts:
    """One closed lease's facts; every row tuple holds only that lease's rows."""

    lease: LeaseRow
    context: LeaseContextRow
    closure: LeaseClosureRow
    spawns: tuple[SpawnRow, ...] = ()
    boundaries: tuple[BoundaryRow, ...] = ()
    usage: tuple[UsageRow, ...] = ()
    session_ends: tuple[SessionEndRow, ...] = ()
    context_samples: tuple[ContextSampleRow, ...] = ()
    parks: tuple[ParkRow, ...] = ()
    park_resumes: tuple[ParkResumeRow, ...] = ()
    pause_parks: tuple[PauseParkRow, ...] = ()
    pause_resumes: tuple[PauseResumeRow, ...] = ()
    overloads: tuple[OverloadRow, ...] = ()
    takeovers: tuple[TakeoverRow, ...] = ()
    takeover_ends: tuple[TakeoverEndRow, ...] = ()
    nudges: tuple[NudgeRow, ...] = ()
    check_results: tuple[CheckResultRow, ...] = ()
    checks_ran: tuple[ChecksRanRow, ...] = ()
