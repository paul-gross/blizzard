"""Factories for the problems the runner, host, egress, trace, analytics, spend, transcript and work-item
operator operations answer. A domain refusal's own text is the detail, byte for byte."""

from blizzard.wire import analytics as analytics_wire
from blizzard.wire import chunk as chunk_wire
from blizzard.wire import egress as egress_wire
from blizzard.wire import fleet as fleet_wire
from blizzard.wire import host as host_wire
from blizzard.wire import runner as runner_wire
from blizzard.wire import traces as traces_wire
from blizzard.wire import transcript_segment as transcript_segment_wire
from blizzard.wire import work_source as work_source_wire
from blizzard.wire.analytics import ReDeriveScopeRefusedProblem
from blizzard.wire.chunk import NodeStepNotFoundProblem
from blizzard.wire.egress import (
    EgressBackfillUnavailableProblem,
    EgressBackfillWindowRefusedProblem,
    EgressResetRefusedProblem,
    EgressResetUnavailableProblem,
)
from blizzard.wire.fleet import SpendInstantInvalidProblem
from blizzard.wire.host import (
    HostHoldsRoutesProblem,
    HostLifecycleConflictProblem,
    HostNotEnrolledProblem,
    HostNotFoundProblem,
    HostRetirementInterruptedProblem,
    HostRetirementUnfinishedProblem,
)
from blizzard.wire.runner import RunnerNotEnrolledProblem, RunnerNotRetiredProblem, RunnerReinstateInterruptedProblem
from blizzard.wire.traces import TraceReplayUnavailableProblem, TraceReplayWindowRefusedProblem
from blizzard.wire.transcript_segment import TranscriptSegmentNotFoundProblem
from blizzard.wire.work_source import WorkItemsChunkIdsRequiredProblem


def host_not_found_problem(host_id: str, *, instance: str) -> HostNotFoundProblem:
    return HostNotFoundProblem(
        type=host_wire.HOST_NOT_FOUND,
        title="Host not found",
        detail=f"unknown host {host_id}",
        instance=instance,
        status=404,
    )


def host_lifecycle_conflict_problem(exc: Exception, *, instance: str) -> HostLifecycleConflictProblem:
    return HostLifecycleConflictProblem(
        type=host_wire.HOST_LIFECYCLE_CONFLICT,
        title="Host lifecycle conflict",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def host_retirement_unfinished_problem(exc: Exception, *, instance: str) -> HostRetirementUnfinishedProblem:
    return HostRetirementUnfinishedProblem(
        type=host_wire.HOST_RETIREMENT_UNFINISHED,
        title="Host retirement unfinished",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def host_not_enrolled_problem(exc: Exception, *, instance: str) -> HostNotEnrolledProblem:
    return HostNotEnrolledProblem(
        type=host_wire.HOST_NOT_ENROLLED, title="Host not enrolled", detail=str(exc), instance=instance, status=409
    )


def host_holds_routes_problem(exc: Exception, *, instance: str) -> HostHoldsRoutesProblem:
    return HostHoldsRoutesProblem(
        type=host_wire.HOST_HOLDS_ROUTES, title="Host holds routes", detail=str(exc), instance=instance, status=409
    )


def host_retirement_interrupted_problem(exc: Exception, *, instance: str) -> HostRetirementInterruptedProblem:
    return HostRetirementInterruptedProblem(
        type=host_wire.HOST_RETIREMENT_INTERRUPTED,
        title="Host retirement interrupted",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def runner_not_retired_problem(exc: Exception, *, instance: str) -> RunnerNotRetiredProblem:
    return RunnerNotRetiredProblem(
        type=runner_wire.RUNNER_NOT_RETIRED, title="Runner not retired", detail=str(exc), instance=instance, status=409
    )


def runner_not_enrolled_problem(exc: Exception, *, instance: str) -> RunnerNotEnrolledProblem:
    return RunnerNotEnrolledProblem(
        type=runner_wire.RUNNER_NOT_ENROLLED,
        title="Runner not enrolled",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def runner_reinstate_interrupted_problem(exc: Exception, *, instance: str) -> RunnerReinstateInterruptedProblem:
    return RunnerReinstateInterruptedProblem(
        type=runner_wire.RUNNER_REINSTATE_INTERRUPTED,
        title="Runner reinstatement interrupted",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def egress_reset_refused_problem(exc: Exception, *, instance: str) -> EgressResetRefusedProblem:
    return EgressResetRefusedProblem(
        type=egress_wire.EGRESS_RESET_REFUSED,
        title="Egress reset refused",
        detail=str(exc),
        instance=instance,
        status=422,
    )


def egress_reset_unavailable_problem(exc: Exception, *, instance: str) -> EgressResetUnavailableProblem:
    return EgressResetUnavailableProblem(
        type=egress_wire.EGRESS_RESET_UNAVAILABLE,
        title="Egress reset unavailable",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def egress_backfill_window_refused_problem(exc: Exception, *, instance: str) -> EgressBackfillWindowRefusedProblem:
    return EgressBackfillWindowRefusedProblem(
        type=egress_wire.EGRESS_BACKFILL_WINDOW_REFUSED,
        title="Egress backfill window refused",
        detail=str(exc),
        instance=instance,
        status=422,
    )


def egress_backfill_unavailable_problem(exc: Exception, *, instance: str) -> EgressBackfillUnavailableProblem:
    return EgressBackfillUnavailableProblem(
        type=egress_wire.EGRESS_BACKFILL_UNAVAILABLE,
        title="Egress backfill unavailable",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def trace_replay_window_refused_problem(exc: Exception, *, instance: str) -> TraceReplayWindowRefusedProblem:
    return TraceReplayWindowRefusedProblem(
        type=traces_wire.TRACE_REPLAY_WINDOW_REFUSED,
        title="Trace replay window refused",
        detail=str(exc),
        instance=instance,
        status=422,
    )


def trace_replay_unavailable_problem(exc: Exception, *, instance: str) -> TraceReplayUnavailableProblem:
    return TraceReplayUnavailableProblem(
        type=traces_wire.TRACE_REPLAY_UNAVAILABLE,
        title="Trace replay unavailable",
        detail=str(exc),
        instance=instance,
        status=409,
    )


def re_derive_scope_refused_problem(exc: Exception, *, instance: str) -> ReDeriveScopeRefusedProblem:
    return ReDeriveScopeRefusedProblem(
        type=analytics_wire.RE_DERIVE_SCOPE_REFUSED,
        title="Re-derive scope refused",
        detail=str(exc),
        instance=instance,
        status=422,
    )


def spend_instant_invalid_problem(field: str, value: str, *, instance: str) -> SpendInstantInvalidProblem:
    return SpendInstantInvalidProblem(
        type=fleet_wire.SPEND_INSTANT_INVALID,
        title="Spend instant invalid",
        detail=f"{field} {value!r} is not a valid ISO-8601 instant",
        instance=instance,
        status=422,
    )


def work_items_chunk_ids_required_problem(*, instance: str) -> WorkItemsChunkIdsRequiredProblem:
    return WorkItemsChunkIdsRequiredProblem(
        type=work_source_wire.WORK_ITEMS_CHUNK_IDS_REQUIRED,
        title="Work items chunk ids required",
        detail="name at least one chunk_id",
        instance=instance,
        status=422,
    )


def transcript_segment_not_found_problem(segment_id: str, *, instance: str) -> TranscriptSegmentNotFoundProblem:
    return TranscriptSegmentNotFoundProblem(
        type=transcript_segment_wire.TRANSCRIPT_SEGMENT_NOT_FOUND,
        title="Transcript segment not found",
        detail=f"unknown segment {segment_id}",
        instance=instance,
        status=404,
    )


def node_step_not_found_problem(chunk_id: str, *, instance: str) -> NodeStepNotFoundProblem:
    return NodeStepNotFoundProblem(
        type=chunk_wire.NODE_STEP_NOT_FOUND,
        title="Node step not found",
        detail=f"no such node step of {chunk_id}",
        instance=instance,
        status=404,
    )
