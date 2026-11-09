"""Factories for the chunk-family refusals. Each carries the refusal's own wording as ``detail``, so the
response body stays what it was before the refusal had a type."""

from typing import Any

from blizzard.wire import chunk as chunk_wire
from blizzard.wire import decision as decision_wire
from blizzard.wire import finding as finding_wire
from blizzard.wire import flow as flow_wire
from blizzard.wire import flow_submissions as flow_submissions_wire
from blizzard.wire import graph as graph_wire
from blizzard.wire import question as question_wire
from blizzard.wire import queue as queue_wire
from blizzard.wire.chunk import (
    BlankEditValueProblem,
    ChunkAlreadyMovedProblem,
    ChunkHasDependentsProblem,
    ChunkNotDeletableProblem,
    ChunkNotDemotableProblem,
    ChunkNotEditableProblem,
    ChunkNotEscalatedProblem,
    ChunkNotGroupableProblem,
    ChunkNotPromotableProblem,
    ChunkNotRestartableProblem,
    ChunkNotRoutedProblem,
    ChunkNotStoppableProblem,
    ChunksInDifferentProjectsProblem,
    FoldWouldCloseCycleProblem,
    IngestEmptyProblem,
    IngestItemProjectMismatchProblem,
    IngestTokenUnclaimedProblem,
    InvalidHarnessesProblem,
)
from blizzard.wire.decision import DecisionClosedProblem, DecisionNotFoundProblem, NotADecisionChoiceProblem
from blizzard.wire.finding import (
    AbsorberNotLiveProblem,
    DuplicateFindingProblem,
    FindingNoteRequiredProblem,
    FindingNotFoundProblem,
    FindingSupersedesItselfProblem,
    FindingTransitionRefusedProblem,
    FindingWriteContendedProblem,
)
from blizzard.wire.flow import FlowNotFoundProblem
from blizzard.wire.flow_submissions import SubmissionNotFoundProblem
from blizzard.wire.graph import (
    CurrentNodeNotOnGraphProblem,
    MigrationTargetIsCurrentPinProblem,
    NodeNotOnGraphProblem,
    RestartGraphPinChangedProblem,
    TargetGraphRetiredProblem,
)
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.question import QuestionClosedProblem, QuestionWriteFencedProblem
from blizzard.wire.queue import (
    ContradictoryPlacementProblem,
    DuplicateReorderIdsProblem,
    NotInListProblem,
    SelfAnchoredMoveProblem,
)


def _problem[P: ProblemDetails[Any]](
    model: type[P], type_: str, title: str, status: int, detail: str, instance: str
) -> P:
    return model(type=type_, title=title, detail=detail, instance=instance, status=status)


def ingest_token_unclaimed_problem(detail: str, *, instance: str) -> IngestTokenUnclaimedProblem:
    return _problem(
        IngestTokenUnclaimedProblem, chunk_wire.INGEST_TOKEN_UNCLAIMED, "Ingest token unclaimed", 422, detail, instance
    )


def ingest_empty_problem(detail: str, *, instance: str) -> IngestEmptyProblem:
    return _problem(IngestEmptyProblem, chunk_wire.INGEST_EMPTY, "Ingest names no work", 422, detail, instance)


def ingest_item_project_mismatch_problem(detail: str, *, instance: str) -> IngestItemProjectMismatchProblem:
    return _problem(
        IngestItemProjectMismatchProblem,
        chunk_wire.INGEST_ITEM_PROJECT_MISMATCH,
        "Ingest item recorded in another project",
        422,
        detail,
        instance,
    )


def default_graph_retired_problem(detail: str, *, instance: str) -> graph_wire.DefaultGraphRetiredProblem:
    return _problem(
        graph_wire.DefaultGraphRetiredProblem,
        graph_wire.DEFAULT_GRAPH_RETIRED,
        "Default graph retired",
        503,
        detail,
        instance,
    )


def chunk_not_escalated_problem(detail: str, *, instance: str) -> ChunkNotEscalatedProblem:
    return _problem(
        ChunkNotEscalatedProblem, chunk_wire.CHUNK_NOT_ESCALATED, "Chunk not escalated", 409, detail, instance
    )


def chunk_not_restartable_problem(detail: str, *, instance: str) -> ChunkNotRestartableProblem:
    return _problem(
        ChunkNotRestartableProblem, chunk_wire.CHUNK_NOT_RESTARTABLE, "Chunk not restartable", 409, detail, instance
    )


def node_not_on_graph_problem(detail: str, *, instance: str) -> NodeNotOnGraphProblem:
    return _problem(NodeNotOnGraphProblem, graph_wire.NODE_NOT_ON_GRAPH, "Node not on graph", 409, detail, instance)


def current_node_not_on_graph_problem(detail: str, *, instance: str) -> CurrentNodeNotOnGraphProblem:
    return _problem(
        CurrentNodeNotOnGraphProblem,
        graph_wire.CURRENT_NODE_NOT_ON_GRAPH,
        "Current node not on graph",
        409,
        detail,
        instance,
    )


def restart_graph_pin_changed_problem(detail: str, *, instance: str) -> RestartGraphPinChangedProblem:
    return _problem(
        RestartGraphPinChangedProblem,
        graph_wire.RESTART_GRAPH_PIN_CHANGED,
        "Restart graph pin changed",
        409,
        detail,
        instance,
    )


def target_graph_retired_problem(detail: str, *, instance: str) -> TargetGraphRetiredProblem:
    return _problem(
        TargetGraphRetiredProblem, graph_wire.TARGET_GRAPH_RETIRED, "Target graph retired", 409, detail, instance
    )


def migration_target_is_current_pin_problem(detail: str, *, instance: str) -> MigrationTargetIsCurrentPinProblem:
    return _problem(
        MigrationTargetIsCurrentPinProblem,
        graph_wire.MIGRATION_TARGET_IS_CURRENT_PIN,
        "Migration target is the current pin",
        409,
        detail,
        instance,
    )


def chunk_not_routed_problem(detail: str, *, instance: str) -> ChunkNotRoutedProblem:
    return _problem(ChunkNotRoutedProblem, chunk_wire.CHUNK_NOT_ROUTED, "Chunk not routed", 409, detail, instance)


def chunk_not_promotable_problem(detail: str, *, instance: str) -> ChunkNotPromotableProblem:
    return _problem(
        ChunkNotPromotableProblem, chunk_wire.CHUNK_NOT_PROMOTABLE, "Chunk not promotable", 409, detail, instance
    )


def chunk_not_demotable_problem(detail: str, *, instance: str) -> ChunkNotDemotableProblem:
    return _problem(
        ChunkNotDemotableProblem, chunk_wire.CHUNK_NOT_DEMOTABLE, "Chunk not demotable", 409, detail, instance
    )


def chunk_not_stoppable_problem(detail: str, *, instance: str) -> ChunkNotStoppableProblem:
    return _problem(
        ChunkNotStoppableProblem, chunk_wire.CHUNK_NOT_STOPPABLE, "Chunk not stoppable", 409, detail, instance
    )


def chunk_not_deletable_problem(detail: str, *, instance: str) -> ChunkNotDeletableProblem:
    return _problem(
        ChunkNotDeletableProblem, chunk_wire.CHUNK_NOT_DELETABLE, "Chunk not deletable", 409, detail, instance
    )


def chunk_has_dependents_problem(detail: str, *, instance: str) -> ChunkHasDependentsProblem:
    return _problem(
        ChunkHasDependentsProblem, chunk_wire.CHUNK_HAS_DEPENDENTS, "Chunk has dependents", 409, detail, instance
    )


def chunk_not_editable_problem(detail: str, *, instance: str) -> ChunkNotEditableProblem:
    return _problem(ChunkNotEditableProblem, chunk_wire.CHUNK_NOT_EDITABLE, "Chunk not editable", 409, detail, instance)


def chunk_already_moved_problem(detail: str, *, instance: str) -> ChunkAlreadyMovedProblem:
    return _problem(
        ChunkAlreadyMovedProblem, chunk_wire.CHUNK_ALREADY_MOVED, "Chunk already moved", 409, detail, instance
    )


def not_in_list_problem(detail: str, *, instance: str) -> NotInListProblem:
    return _problem(NotInListProblem, queue_wire.NOT_IN_LIST, "Chunk not in list", 409, detail, instance)


def self_anchored_move_problem(detail: str, *, instance: str) -> SelfAnchoredMoveProblem:
    return _problem(SelfAnchoredMoveProblem, queue_wire.SELF_ANCHORED_MOVE, "Self-anchored move", 422, detail, instance)


def contradictory_placement_problem(detail: str, *, instance: str) -> ContradictoryPlacementProblem:
    return _problem(
        ContradictoryPlacementProblem,
        queue_wire.CONTRADICTORY_PLACEMENT,
        "Contradictory placement",
        422,
        detail,
        instance,
    )


def duplicate_reorder_ids_problem(detail: str, *, instance: str) -> DuplicateReorderIdsProblem:
    return _problem(
        DuplicateReorderIdsProblem, queue_wire.DUPLICATE_REORDER_IDS, "Duplicate reorder ids", 422, detail, instance
    )


def chunk_not_groupable_problem(detail: str, *, instance: str) -> ChunkNotGroupableProblem:
    return _problem(
        ChunkNotGroupableProblem, chunk_wire.CHUNK_NOT_GROUPABLE, "Chunk not groupable", 409, detail, instance
    )


def chunks_in_different_projects_problem(detail: str, *, instance: str) -> ChunksInDifferentProjectsProblem:
    return _problem(
        ChunksInDifferentProjectsProblem,
        chunk_wire.CHUNKS_IN_DIFFERENT_PROJECTS,
        "Chunks in different projects",
        409,
        detail,
        instance,
    )


def fold_would_close_cycle_problem(detail: str, *, instance: str) -> FoldWouldCloseCycleProblem:
    return _problem(
        FoldWouldCloseCycleProblem, chunk_wire.FOLD_WOULD_CLOSE_CYCLE, "Fold would close a cycle", 409, detail, instance
    )


def blank_edit_value_problem(detail: str, *, instance: str) -> BlankEditValueProblem:
    return _problem(BlankEditValueProblem, chunk_wire.BLANK_EDIT_VALUE, "Blank edit value", 422, detail, instance)


def invalid_harnesses_problem(detail: str, *, instance: str) -> InvalidHarnessesProblem:
    return _problem(
        InvalidHarnessesProblem, chunk_wire.INVALID_HARNESSES, "Invalid harness preference", 422, detail, instance
    )


def graph_not_found_problem(detail: str, *, instance: str) -> graph_wire.GraphNotFoundProblem:
    return _problem(
        graph_wire.GraphNotFoundProblem, graph_wire.GRAPH_NOT_FOUND, "Graph not found", 404, detail, instance
    )


def decision_not_found_problem(detail: str, *, instance: str) -> DecisionNotFoundProblem:
    return _problem(
        DecisionNotFoundProblem, decision_wire.DECISION_NOT_FOUND, "Decision not found", 404, detail, instance
    )


def not_a_decision_choice_problem(detail: str, *, instance: str) -> NotADecisionChoiceProblem:
    return _problem(
        NotADecisionChoiceProblem, decision_wire.NOT_A_DECISION_CHOICE, "Not a decision choice", 400, detail, instance
    )


def decision_closed_problem(detail: str, *, instance: str) -> DecisionClosedProblem:
    return _problem(DecisionClosedProblem, decision_wire.DECISION_CLOSED, "Decision closed", 409, detail, instance)


def question_write_fenced_problem(detail: str, *, instance: str) -> QuestionWriteFencedProblem:
    return _problem(
        QuestionWriteFencedProblem,
        question_wire.QUESTION_WRITE_FENCED,
        "Question write fenced out",
        409,
        detail,
        instance,
    )


def question_closed_problem(detail: str, *, instance: str) -> QuestionClosedProblem:
    return _problem(QuestionClosedProblem, question_wire.QUESTION_CLOSED, "Question closed", 409, detail, instance)


def finding_not_found_problem(detail: str, *, instance: str) -> FindingNotFoundProblem:
    return _problem(FindingNotFoundProblem, finding_wire.FINDING_NOT_FOUND, "Finding not found", 404, detail, instance)


def finding_transition_refused_problem(detail: str, *, instance: str) -> FindingTransitionRefusedProblem:
    return _problem(
        FindingTransitionRefusedProblem,
        finding_wire.FINDING_TRANSITION_REFUSED,
        "Finding transition refused",
        409,
        detail,
        instance,
    )


def finding_write_contended_problem(detail: str, *, instance: str) -> FindingWriteContendedProblem:
    return _problem(
        FindingWriteContendedProblem,
        finding_wire.FINDING_WRITE_CONTENDED,
        "Finding write contended",
        409,
        detail,
        instance,
    )


def finding_note_required_problem(detail: str, *, instance: str) -> FindingNoteRequiredProblem:
    return _problem(
        FindingNoteRequiredProblem, finding_wire.FINDING_NOTE_REQUIRED, "Finding note required", 422, detail, instance
    )


def duplicate_finding_problem(detail: str, *, instance: str) -> DuplicateFindingProblem:
    return _problem(DuplicateFindingProblem, finding_wire.DUPLICATE_FINDING, "Duplicate finding", 422, detail, instance)


def finding_supersedes_itself_problem(detail: str, *, instance: str) -> FindingSupersedesItselfProblem:
    return _problem(
        FindingSupersedesItselfProblem,
        finding_wire.FINDING_SUPERSEDES_ITSELF,
        "Finding supersedes itself",
        422,
        detail,
        instance,
    )


def absorber_not_live_problem(detail: str, *, instance: str) -> AbsorberNotLiveProblem:
    return _problem(AbsorberNotLiveProblem, finding_wire.ABSORBER_NOT_LIVE, "Absorber not live", 422, detail, instance)


def submission_not_found_problem(detail: str, *, instance: str) -> SubmissionNotFoundProblem:
    return _problem(
        SubmissionNotFoundProblem,
        flow_submissions_wire.SUBMISSION_NOT_FOUND,
        "Submission not found",
        404,
        detail,
        instance,
    )


def flow_not_found_problem(detail: str, *, instance: str) -> FlowNotFoundProblem:
    return _problem(FlowNotFoundProblem, flow_wire.FLOW_NOT_FOUND, "Flow not found", 404, detail, instance)
