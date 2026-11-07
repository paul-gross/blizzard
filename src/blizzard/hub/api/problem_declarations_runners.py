"""The problems the runner, host, egress, trace, analytics, spend, transcript and work-item operator operations
declare, by status."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.api.problem_declaration import ProblemDeclaration
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.host import HostRetiredProblem
from blizzard.wire.problem_types_runners import (
    EgressBackfillUnavailableProblem,
    EgressBackfillWindowRefusedProblem,
    EgressResetRefusedProblem,
    EgressResetUnavailableProblem,
    HostHoldsRoutesProblem,
    HostLifecycleConflictProblem,
    HostNotEnrolledProblem,
    HostNotFoundProblem,
    HostRetirementInterruptedProblem,
    HostRetirementUnfinishedProblem,
    ReDeriveScopeRefusedProblem,
    RunnerNotEnrolledProblem,
    RunnerNotRetiredProblem,
    RunnerReinstateInterruptedProblem,
    SpendInstantInvalidProblem,
    TraceReplayUnavailableProblem,
    TraceReplayWindowRefusedProblem,
    TranscriptSegmentNotFoundProblem,
)
from blizzard.wire.route import MalformedCursorProblem
from blizzard.wire.runner import (
    RunnerAlreadyHostedProblem,
    RunnerHoldsRoutesProblem,
    RunnerLifecycleConflictProblem,
    RunnerNotFoundProblem,
    RunnerRetiredProblem,
    RunnerRetireInterruptedProblem,
    UnknownServedProjectProblem,
)

_RUNNER = "/api/runners/{runner_id}"
_HOST = "/api/hosts/{host_id}"


def _declare(method: str, path: str, status: int, *models: type[BaseModel]) -> tuple[ProblemDeclaration, ...]:
    return tuple(ProblemDeclaration(method, path, status, model) for model in models)


RUNNERS_DECLARATIONS: tuple[ProblemDeclaration, ...] = (
    *_declare("POST", "/api/runners", 404, HostNotFoundProblem),
    *_declare("POST", "/api/runners", 422, UnknownServedProjectProblem),
    *_declare("POST", "/api/runners", 409, HostRetiredProblem),
    *(
        declaration
        for method, path in (
            ("GET", _RUNNER),
            ("POST", f"{_RUNNER}/enrollments"),
            ("POST", f"{_RUNNER}/pause"),
            ("POST", f"{_RUNNER}/resume"),
            ("POST", f"{_RUNNER}/retire"),
            ("POST", f"{_RUNNER}/reinstate"),
            ("POST", f"{_RUNNER}/token-revocations"),
        )
        for declaration in _declare(method, path, 404, RunnerNotFoundProblem)
    ),
    *_declare("POST", f"{_RUNNER}/enrollments", 409, RunnerRetiredProblem),
    *_declare(
        "POST",
        f"{_RUNNER}/retire",
        409,
        RunnerRetireInterruptedProblem,
        RunnerHoldsRoutesProblem,
        RunnerLifecycleConflictProblem,
    ),
    *_declare(
        "POST",
        f"{_RUNNER}/reinstate",
        409,
        RunnerReinstateInterruptedProblem,
        RunnerNotRetiredProblem,
        HostRetiredProblem,
        RunnerLifecycleConflictProblem,
    ),
    *_declare("POST", f"{_RUNNER}/token-revocations", 409, RunnerNotEnrolledProblem, RunnerLifecycleConflictProblem),
    *(
        declaration
        for method, path in (
            ("GET", _HOST),
            ("POST", f"{_HOST}/adoptions"),
            ("POST", f"{_HOST}/releases"),
            ("POST", f"{_HOST}/enrollments"),
            ("POST", f"{_HOST}/token-revocations"),
            ("POST", f"{_HOST}/retire"),
        )
        for declaration in _declare(method, path, 404, HostNotFoundProblem)
    ),
    *(
        declaration
        for path in (f"{_HOST}/adoptions", f"{_HOST}/releases")
        for declaration in _declare("POST", path, 404, RunnerNotFoundProblem)
    ),
    *_declare(
        "POST",
        f"{_HOST}/adoptions",
        409,
        RunnerAlreadyHostedProblem,
        RunnerRetiredProblem,
        HostRetiredProblem,
        RunnerLifecycleConflictProblem,
    ),
    *_declare(
        "POST",
        f"{_HOST}/releases",
        409,
        HostRetirementUnfinishedProblem,
        HostLifecycleConflictProblem,
        RunnerLifecycleConflictProblem,
    ),
    *_declare("POST", f"{_HOST}/enrollments", 409, HostRetiredProblem),
    *_declare("POST", f"{_HOST}/token-revocations", 409, HostNotEnrolledProblem),
    *_declare(
        "POST",
        f"{_HOST}/retire",
        409,
        HostHoldsRoutesProblem,
        HostLifecycleConflictProblem,
        HostRetirementInterruptedProblem,
    ),
    *_declare("POST", "/api/egress/reset", 422, EgressResetRefusedProblem),
    *_declare("POST", "/api/egress/reset", 409, EgressResetUnavailableProblem),
    *_declare("POST", "/api/egress/backfill", 422, EgressBackfillWindowRefusedProblem),
    *_declare("POST", "/api/egress/backfill", 409, EgressBackfillUnavailableProblem),
    *_declare("POST", "/api/traces/replay", 422, TraceReplayWindowRefusedProblem),
    *_declare("POST", "/api/traces/replay", 409, TraceReplayUnavailableProblem),
    *_declare("POST", "/api/analytics/re-derive", 422, ReDeriveScopeRefusedProblem),
    *(
        declaration
        for path in (
            "/api/analytics/events",
            "/api/analytics/events/ndjson",
            "/api/analytics/spend/chunks",
            "/api/analytics/spend/chunks/ndjson",
        )
        for declaration in _declare("GET", path, 422, MalformedCursorProblem)
    ),
    *_declare("GET", "/api/spend", 422, SpendInstantInvalidProblem),
    *(
        declaration
        for path in ("/api/chunks/{chunk_id}/transcripts", "/api/chunks/{chunk_id}/transcripts/{segment_id}")
        for declaration in _declare("GET", path, 404, ChunkNotFoundProblem)
    ),
    *_declare("GET", "/api/chunks/{chunk_id}/transcripts/{segment_id}", 404, TranscriptSegmentNotFoundProblem),
)
