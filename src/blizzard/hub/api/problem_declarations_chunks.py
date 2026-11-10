"""Which problem variants the chunk-family operations declare: chunks, their edit and dependency verbs, the
queue and backlog, decisions, questions, findings and flows. The integrator unions them into the hub registry."""

from blizzard.hub.api.problem_declaration import ProblemDeclaration
from blizzard.wire import chunk as chunk_wire
from blizzard.wire import decision as decision_wire
from blizzard.wire import finding as finding_wire
from blizzard.wire import flow as flow_wire
from blizzard.wire import flow_submissions as flow_submissions_wire
from blizzard.wire import graph as graph_wire
from blizzard.wire import question as question_wire
from blizzard.wire import queue as queue_wire
from blizzard.wire.chunk import ChunkNotFoundProblem, ChunkNotPausableProblem
from blizzard.wire.question import QuestionNotFoundProblem
from blizzard.wire.route import MalformedCursorProblem, PinnedGraphMissingProblem

_CHUNK_READS = (
    "/api/chunks/{chunk_id}",
    "/api/chunks/{chunk_id}/close-diagnostic",
    "/api/chunks/{chunk_id}/landing-facts",
    "/api/chunks/{chunk_id}/work-items",
)
_CHUNK_WRITES = (
    ("POST", "/api/chunks/{chunk_id}/requeues"),
    ("POST", "/api/chunks/{chunk_id}/restart"),
    ("POST", "/api/chunks/{chunk_id}/detach"),
    ("POST", "/api/chunks/{chunk_id}/pause"),
    ("POST", "/api/chunks/{chunk_id}/resume"),
    ("POST", "/api/chunks/{chunk_id}/stop"),
    ("POST", "/api/chunks/{chunk_id}/complete"),
    ("POST", "/api/chunks/{chunk_id}/promote"),
    ("POST", "/api/chunks/{chunk_id}/demote"),
    ("POST", "/api/chunks/{chunk_id}/group"),
    ("POST", "/api/chunks/{chunk_id}/dependencies"),
    ("POST", "/api/chunks/{chunk_id}/dependencies/release"),
    ("PATCH", "/api/chunks/{chunk_id}"),
    ("DELETE", "/api/chunks/{chunk_id}"),
)
_PLACEMENTS = ("/api/chunks/{chunk_id}/promote", "/api/chunks/{chunk_id}/demote")
_FINDING_VERBS = ("resolve", "confirm-gone", "wont-fix", "not-a-finding", "supersede", "reopen")

CHUNKS_DECLARATIONS: tuple[ProblemDeclaration, ...] = (
    # Chunk lookups.
    *(ProblemDeclaration("GET", path, 404, ChunkNotFoundProblem) for path in _CHUNK_READS),
    *(ProblemDeclaration(method, path, 404, ChunkNotFoundProblem) for method, path in _CHUNK_WRITES),
    ProblemDeclaration("POST", "/api/questions", 404, ChunkNotFoundProblem),
    ProblemDeclaration("GET", "/api/chunks", 422, MalformedCursorProblem),
    ProblemDeclaration("GET", "/api/findings", 422, MalformedCursorProblem),
    ProblemDeclaration("GET", "/api/projects/{project}/findings", 422, MalformedCursorProblem),
    # Ingest.
    *(
        ProblemDeclaration("POST", "/api/chunks", 422, model)
        for model in (
            chunk_wire.IngestTokenUnclaimedProblem,
            chunk_wire.IngestEmptyProblem,
            chunk_wire.IngestItemProjectMismatchProblem,
        )
    ),
    ProblemDeclaration("POST", "/api/chunks", 503, graph_wire.DefaultGraphRetiredProblem),
    # Lifecycle verbs.
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/pause", 409, ChunkNotPausableProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/requeues", 409, chunk_wire.ChunkNotEscalatedProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/restart", 500, PinnedGraphMissingProblem),
    *(
        ProblemDeclaration("POST", "/api/chunks/{chunk_id}/restart", 409, model)
        for model in (
            chunk_wire.ChunkNotRestartableProblem,
            graph_wire.NodeNotOnGraphProblem,
            graph_wire.CurrentNodeNotOnGraphProblem,
            graph_wire.RestartGraphPinChangedProblem,
            graph_wire.TargetGraphRetiredProblem,
            graph_wire.MigrationTargetIsCurrentPinProblem,
        )
    ),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/detach", 409, chunk_wire.ChunkNotRoutedProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/stop", 409, chunk_wire.ChunkNotStoppableProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/promote", 409, chunk_wire.ChunkNotPromotableProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/demote", 409, chunk_wire.ChunkNotDemotableProblem),
    *(
        ProblemDeclaration("POST", path, status, model)
        for path in _PLACEMENTS
        for status, model in (
            (409, queue_wire.NotInListProblem),
            (422, queue_wire.SelfAnchoredMoveProblem),
            (422, queue_wire.ContradictoryPlacementProblem),
        )
    ),
    # Edit and delete.
    *(
        ProblemDeclaration("PATCH", "/api/chunks/{chunk_id}", 409, model)
        for model in (
            chunk_wire.ChunkNotEditableProblem,
            chunk_wire.ChunkAlreadyMovedProblem,
            graph_wire.TargetGraphRetiredProblem,
            graph_wire.MigrationTargetIsCurrentPinProblem,
            graph_wire.NodeNotOnGraphProblem,
        )
    ),
    *(
        ProblemDeclaration("PATCH", "/api/chunks/{chunk_id}", 422, model)
        for model in (chunk_wire.BlankEditValueProblem, chunk_wire.InvalidHarnessesProblem)
    ),
    ProblemDeclaration("PATCH", "/api/chunks/{chunk_id}", 404, graph_wire.GraphNotFoundProblem),
    *(
        ProblemDeclaration("DELETE", "/api/chunks/{chunk_id}", 409, model)
        for model in (chunk_wire.ChunkNotDeletableProblem, chunk_wire.ChunkHasDependentsProblem)
    ),
    # Queue and backlog.
    *(
        ProblemDeclaration("PUT", path, 422, queue_wire.DuplicateReorderIdsProblem)
        for path in ("/api/queue", "/api/backlog")
    ),
    *(
        ProblemDeclaration("POST", path, 422, queue_wire.SelfAnchoredMoveProblem)
        for path in ("/api/queue/position", "/api/backlog/position")
    ),
    *(
        ProblemDeclaration(method, path, 409, queue_wire.NotInListProblem)
        for method, path in (
            ("PUT", "/api/queue"),
            ("PUT", "/api/backlog"),
            ("POST", "/api/queue/position"),
            ("POST", "/api/backlog/position"),
        )
    ),
    *(
        ProblemDeclaration("POST", "/api/chunks/{chunk_id}/group", 409, model)
        for model in (
            chunk_wire.ChunkNotGroupableProblem,
            chunk_wire.ChunksInDifferentProjectsProblem,
            chunk_wire.FoldWouldCloseCycleProblem,
        )
    ),
    # Decisions and questions.
    ProblemDeclaration("POST", "/api/decisions/{decision_id}/resolutions", 404, decision_wire.DecisionNotFoundProblem),
    ProblemDeclaration(
        "POST", "/api/decisions/{decision_id}/resolutions", 400, decision_wire.NotADecisionChoiceProblem
    ),
    ProblemDeclaration("POST", "/api/decisions/{decision_id}/resolutions", 409, decision_wire.DecisionClosedProblem),
    ProblemDeclaration("POST", "/api/questions", 409, question_wire.QuestionWriteFencedProblem),
    ProblemDeclaration("POST", "/api/questions/{question_id}/answers", 404, QuestionNotFoundProblem),
    ProblemDeclaration("POST", "/api/questions/{question_id}/answers", 409, question_wire.QuestionClosedProblem),
    # Findings.
    ProblemDeclaration("GET", "/api/findings/{finding_id}", 404, finding_wire.FindingNotFoundProblem),
    *(
        ProblemDeclaration("POST", f"/api/findings/{verb}", status, model)
        for verb in _FINDING_VERBS
        for status, model in (
            (404, finding_wire.FindingNotFoundProblem),
            (409, finding_wire.FindingTransitionRefusedProblem),
            (409, finding_wire.FindingWriteContendedProblem),
            (422, finding_wire.FindingNoteRequiredProblem),
            (422, finding_wire.DuplicateFindingProblem),
        )
    ),
    ProblemDeclaration("POST", "/api/findings/supersede", 422, finding_wire.FindingSupersedesItselfProblem),
    ProblemDeclaration("POST", "/api/findings/supersede", 422, finding_wire.AbsorberNotLiveProblem),
    # Flows.
    *(
        ProblemDeclaration("GET", path, 404, flow_wire.FlowNotFoundProblem)
        for path in ("/api/flows/{flow_id}", "/api/flows/{flow_id}/record")
    ),
    ProblemDeclaration(
        "GET",
        "/api/chunks/{chunk_id}/flow-submissions/{submission_id}/turns",
        404,
        flow_submissions_wire.SubmissionNotFoundProblem,
    ),
)
