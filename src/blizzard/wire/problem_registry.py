"""Integrate the concept-local problem type contributions."""

from blizzard.wire.analytics import ANALYTICS_PROBLEM_TYPES
from blizzard.wire.auth import AUTH_PROBLEM_TYPES
from blizzard.wire.cadence import CADENCE_PROBLEM_TYPES
from blizzard.wire.chunk import CHUNK_PROBLEM_TYPES
from blizzard.wire.config import CONFIG_PROBLEM_TYPES
from blizzard.wire.decision import DECISION_PROBLEM_TYPES
from blizzard.wire.egress import EGRESS_PROBLEM_TYPES
from blizzard.wire.finding import FINDING_PROBLEM_TYPES
from blizzard.wire.flags import FLAGS_PROBLEM_TYPES
from blizzard.wire.fleet import FLEET_PROBLEM_TYPES
from blizzard.wire.flow import FLOW_PROBLEM_TYPES
from blizzard.wire.flow_submissions import FLOW_SUBMISSIONS_PROBLEM_TYPES
from blizzard.wire.forge import FORGE_PROBLEM_TYPES
from blizzard.wire.garden_proposal import GARDEN_PROPOSAL_PROBLEM_TYPES
from blizzard.wire.garden_run import GARDEN_RUN_PROBLEM_TYPES
from blizzard.wire.garden_sweeps import GARDEN_SWEEPS_PROBLEM_TYPES
from blizzard.wire.garden_window import GARDEN_WINDOW_PROBLEM_TYPES
from blizzard.wire.graph import GRAPH_PROBLEM_TYPES
from blizzard.wire.host import HOST_PROBLEM_TYPES
from blizzard.wire.invariants import INVARIANTS_PROBLEM_TYPES
from blizzard.wire.invitation import INVITATION_PROBLEM_TYPES
from blizzard.wire.problem import PROBLEM_TYPES
from blizzard.wire.project import PROJECT_PROBLEM_TYPES
from blizzard.wire.question import QUESTION_PROBLEM_TYPES
from blizzard.wire.queue import QUEUE_PROBLEM_TYPES
from blizzard.wire.release import RELEASE_PROBLEM_TYPES
from blizzard.wire.repository import REPOSITORY_PROBLEM_TYPES
from blizzard.wire.route import ROUTE_PROBLEM_TYPES
from blizzard.wire.routine import ROUTINE_PROBLEM_TYPES
from blizzard.wire.runner import RUNNER_PROBLEM_TYPES
from blizzard.wire.scope import SCOPE_PROBLEM_TYPES
from blizzard.wire.secret import SECRET_PROBLEM_TYPES
from blizzard.wire.system_artifact import SYSTEM_ARTIFACT_PROBLEM_TYPES
from blizzard.wire.tenant import TENANT_PROBLEM_TYPES
from blizzard.wire.traces import TRACES_PROBLEM_TYPES
from blizzard.wire.transcript_segment import TRANSCRIPT_SEGMENT_PROBLEM_TYPES
from blizzard.wire.urgency import URGENCY_PROBLEM_TYPES
from blizzard.wire.user import USER_PROBLEM_TYPES
from blizzard.wire.work_source import WORK_SOURCE_PROBLEM_TYPES

ALL_PROBLEM_TYPES: frozenset[str] = (
    PROBLEM_TYPES
    | ANALYTICS_PROBLEM_TYPES
    | AUTH_PROBLEM_TYPES
    | CADENCE_PROBLEM_TYPES
    | CHUNK_PROBLEM_TYPES
    | CONFIG_PROBLEM_TYPES
    | DECISION_PROBLEM_TYPES
    | EGRESS_PROBLEM_TYPES
    | FINDING_PROBLEM_TYPES
    | FLAGS_PROBLEM_TYPES
    | FLEET_PROBLEM_TYPES
    | FLOW_PROBLEM_TYPES
    | FLOW_SUBMISSIONS_PROBLEM_TYPES
    | FORGE_PROBLEM_TYPES
    | GARDEN_PROPOSAL_PROBLEM_TYPES
    | GARDEN_RUN_PROBLEM_TYPES
    | GARDEN_SWEEPS_PROBLEM_TYPES
    | GARDEN_WINDOW_PROBLEM_TYPES
    | GRAPH_PROBLEM_TYPES
    | HOST_PROBLEM_TYPES
    | INVARIANTS_PROBLEM_TYPES
    | INVITATION_PROBLEM_TYPES
    | PROJECT_PROBLEM_TYPES
    | QUESTION_PROBLEM_TYPES
    | QUEUE_PROBLEM_TYPES
    | RELEASE_PROBLEM_TYPES
    | REPOSITORY_PROBLEM_TYPES
    | ROUTE_PROBLEM_TYPES
    | ROUTINE_PROBLEM_TYPES
    | RUNNER_PROBLEM_TYPES
    | SCOPE_PROBLEM_TYPES
    | SECRET_PROBLEM_TYPES
    | SYSTEM_ARTIFACT_PROBLEM_TYPES
    | TENANT_PROBLEM_TYPES
    | TRACES_PROBLEM_TYPES
    | TRANSCRIPT_SEGMENT_PROBLEM_TYPES
    | URGENCY_PROBLEM_TYPES
    | USER_PROBLEM_TYPES
    | WORK_SOURCE_PROBLEM_TYPES
)
