"""Wire problems for the runner, host, egress, trace, analytics, spend, transcript and work-item operator
reads and writes: the stable type identifiers and the models that carry them."""

from typing import Final, Literal

from blizzard.wire.problem import ProblemDetails

HOST_NOT_FOUND: Final = "urn:blizzard:problem:host-not-found"
HOST_LIFECYCLE_CONFLICT: Final = "urn:blizzard:problem:host-lifecycle-conflict"
HOST_RETIREMENT_UNFINISHED: Final = "urn:blizzard:problem:host-retirement-unfinished"
HOST_NOT_ENROLLED: Final = "urn:blizzard:problem:host-not-enrolled"
HOST_HOLDS_ROUTES: Final = "urn:blizzard:problem:host-holds-routes"
HOST_RETIREMENT_INTERRUPTED: Final = "urn:blizzard:problem:host-retirement-interrupted"
RUNNER_NOT_RETIRED: Final = "urn:blizzard:problem:runner-not-retired"
RUNNER_NOT_ENROLLED: Final = "urn:blizzard:problem:runner-not-enrolled"
RUNNER_REINSTATE_INTERRUPTED: Final = "urn:blizzard:problem:runner-reinstate-interrupted"
EGRESS_RESET_REFUSED: Final = "urn:blizzard:problem:egress-reset-refused"
EGRESS_RESET_UNAVAILABLE: Final = "urn:blizzard:problem:egress-reset-unavailable"
EGRESS_BACKFILL_WINDOW_REFUSED: Final = "urn:blizzard:problem:egress-backfill-window-refused"
EGRESS_BACKFILL_UNAVAILABLE: Final = "urn:blizzard:problem:egress-backfill-unavailable"
TRACE_REPLAY_WINDOW_REFUSED: Final = "urn:blizzard:problem:trace-replay-window-refused"
TRACE_REPLAY_UNAVAILABLE: Final = "urn:blizzard:problem:trace-replay-unavailable"
RE_DERIVE_SCOPE_REFUSED: Final = "urn:blizzard:problem:re-derive-scope-refused"
SPEND_INSTANT_INVALID: Final = "urn:blizzard:problem:spend-instant-invalid"
WORK_ITEMS_CHUNK_IDS_REQUIRED: Final = "urn:blizzard:problem:work-items-chunk-ids-required"
TRANSCRIPT_SEGMENT_NOT_FOUND: Final = "urn:blizzard:problem:transcript-segment-not-found"

RUNNERS_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        HOST_NOT_FOUND,
        HOST_LIFECYCLE_CONFLICT,
        HOST_RETIREMENT_UNFINISHED,
        HOST_NOT_ENROLLED,
        HOST_HOLDS_ROUTES,
        HOST_RETIREMENT_INTERRUPTED,
        RUNNER_NOT_RETIRED,
        RUNNER_NOT_ENROLLED,
        RUNNER_REINSTATE_INTERRUPTED,
        EGRESS_RESET_REFUSED,
        EGRESS_RESET_UNAVAILABLE,
        EGRESS_BACKFILL_WINDOW_REFUSED,
        EGRESS_BACKFILL_UNAVAILABLE,
        TRACE_REPLAY_WINDOW_REFUSED,
        TRACE_REPLAY_UNAVAILABLE,
        RE_DERIVE_SCOPE_REFUSED,
        SPEND_INSTANT_INVALID,
        WORK_ITEMS_CHUNK_IDS_REQUIRED,
        TRANSCRIPT_SEGMENT_NOT_FOUND,
    }
)


class HostNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:host-not-found"]]):
    """The named host is not available to the caller."""


class HostLifecycleConflictProblem(ProblemDetails[Literal["urn:blizzard:problem:host-lifecycle-conflict"]]):
    """The host's lifecycle changed during the operation."""


class HostRetirementUnfinishedProblem(ProblemDetails[Literal["urn:blizzard:problem:host-retirement-unfinished"]]):
    """The host's retirement has not finished, so its runners cannot be released."""


class HostNotEnrolledProblem(ProblemDetails[Literal["urn:blizzard:problem:host-not-enrolled"]]):
    """The host holds no token to revoke."""


class HostHoldsRoutesProblem(ProblemDetails[Literal["urn:blizzard:problem:host-holds-routes"]]):
    """A non-forced retirement cannot proceed while a runner under the host holds routes."""


class HostRetirementInterruptedProblem(ProblemDetails[Literal["urn:blizzard:problem:host-retirement-interrupted"]]):
    """A host retirement was interrupted after committing part of its work."""


class RunnerNotRetiredProblem(ProblemDetails[Literal["urn:blizzard:problem:runner-not-retired"]]):
    """Only a retired runner can be reinstated."""


class RunnerNotEnrolledProblem(ProblemDetails[Literal["urn:blizzard:problem:runner-not-enrolled"]]):
    """The runner holds no token to revoke."""


class RunnerReinstateInterruptedProblem(ProblemDetails[Literal["urn:blizzard:problem:runner-reinstate-interrupted"]]):
    """A runner reinstatement was interrupted after committing part of its work."""


class EgressResetRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:egress-reset-refused"]]):
    """The dataset is unconfigured or the instant is in the future."""


class EgressResetUnavailableProblem(ProblemDetails[Literal["urn:blizzard:problem:egress-reset-unavailable"]]):
    """The export is off or rejected, so a cursor cannot be reset."""


class EgressBackfillWindowRefusedProblem(
    ProblemDetails[Literal["urn:blizzard:problem:egress-backfill-window-refused"]]
):
    """The backfill window is inverted, too wide, or reaches past now, or the dataset is unknown."""


class EgressBackfillUnavailableProblem(ProblemDetails[Literal["urn:blizzard:problem:egress-backfill-unavailable"]]):
    """The export is off or rejected, so a wet backfill cannot run."""


class TraceReplayWindowRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:trace-replay-window-refused"]]):
    """The replay window is inverted, too wide, or reaches past now."""


class TraceReplayUnavailableProblem(ProblemDetails[Literal["urn:blizzard:problem:trace-replay-unavailable"]]):
    """Tracing is off, so a replay that must export cannot run."""


class ReDeriveScopeRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:re-derive-scope-refused"]]):
    """A re-derive names both a segment and a chunk."""


class SpendInstantInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:spend-instant-invalid"]]):
    """A spend window edge is not an ISO-8601 instant."""


class WorkItemsChunkIdsRequiredProblem(ProblemDetails[Literal["urn:blizzard:problem:work-items-chunk-ids-required"]]):
    """A bulk work-item read named no chunk."""


class TranscriptSegmentNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:transcript-segment-not-found"]]):
    """The chunk holds no transcript segment of that id."""
