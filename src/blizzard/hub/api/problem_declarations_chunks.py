"""Which problem variants the chunk-family operations declare: chunks, their edit and dependency verbs, the
queue and backlog, decisions, questions, findings and flows. The integrator unions them into the hub registry."""

from blizzard.hub.api.problem_declaration import ProblemDeclaration
from blizzard.wire import problem_types_admin as admin_t
from blizzard.wire import problem_types_chunks as t
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
        for model in (t.IngestTokenUnclaimedProblem, t.IngestEmptyProblem, t.IngestItemProjectMismatchProblem)
    ),
    ProblemDeclaration("POST", "/api/chunks", 503, admin_t.DefaultGraphRetiredProblem),
    # Lifecycle verbs.
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/pause", 409, ChunkNotPausableProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/requeues", 409, t.ChunkNotEscalatedProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/restart", 500, PinnedGraphMissingProblem),
    *(
        ProblemDeclaration("POST", "/api/chunks/{chunk_id}/restart", 409, model)
        for model in (
            t.ChunkNotRestartableProblem,
            t.NodeNotOnGraphProblem,
            t.CurrentNodeNotOnGraphProblem,
            t.RestartGraphPinChangedProblem,
            t.TargetGraphRetiredProblem,
            t.MigrationTargetIsCurrentPinProblem,
        )
    ),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/detach", 409, t.ChunkNotRoutedProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/stop", 409, t.ChunkNotStoppableProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/promote", 409, t.ChunkNotPromotableProblem),
    ProblemDeclaration("POST", "/api/chunks/{chunk_id}/demote", 409, t.ChunkNotDemotableProblem),
    *(
        ProblemDeclaration("POST", path, status, model)
        for path in _PLACEMENTS
        for status, model in (
            (409, t.NotInListProblem),
            (422, t.SelfAnchoredMoveProblem),
            (422, t.ContradictoryPlacementProblem),
        )
    ),
    # Edit and delete.
    *(
        ProblemDeclaration("PATCH", "/api/chunks/{chunk_id}", 409, model)
        for model in (
            t.ChunkNotEditableProblem,
            t.ChunkAlreadyMovedProblem,
            t.TargetGraphRetiredProblem,
            t.MigrationTargetIsCurrentPinProblem,
            t.NodeNotOnGraphProblem,
        )
    ),
    *(
        ProblemDeclaration("PATCH", "/api/chunks/{chunk_id}", 422, model)
        for model in (t.BlankEditValueProblem, t.InvalidHarnessesProblem)
    ),
    ProblemDeclaration("PATCH", "/api/chunks/{chunk_id}", 404, admin_t.GraphNotFoundProblem),
    *(
        ProblemDeclaration("DELETE", "/api/chunks/{chunk_id}", 409, model)
        for model in (t.ChunkNotDeletableProblem, t.ChunkHasDependentsProblem)
    ),
    # Queue and backlog.
    *(ProblemDeclaration("PUT", path, 422, t.DuplicateReorderIdsProblem) for path in ("/api/queue", "/api/backlog")),
    *(
        ProblemDeclaration("POST", path, 422, t.SelfAnchoredMoveProblem)
        for path in ("/api/queue/position", "/api/backlog/position")
    ),
    *(
        ProblemDeclaration(method, path, 409, t.NotInListProblem)
        for method, path in (
            ("PUT", "/api/queue"),
            ("PUT", "/api/backlog"),
            ("POST", "/api/queue/position"),
            ("POST", "/api/backlog/position"),
        )
    ),
    *(
        ProblemDeclaration("POST", "/api/chunks/{chunk_id}/group", 409, model)
        for model in (t.ChunkNotGroupableProblem, t.ChunksInDifferentProjectsProblem, t.FoldWouldCloseCycleProblem)
    ),
    # Decisions and questions.
    ProblemDeclaration("POST", "/api/decisions/{decision_id}/resolutions", 404, t.DecisionNotFoundProblem),
    ProblemDeclaration("POST", "/api/decisions/{decision_id}/resolutions", 400, t.NotADecisionChoiceProblem),
    ProblemDeclaration("POST", "/api/decisions/{decision_id}/resolutions", 409, t.DecisionClosedProblem),
    ProblemDeclaration("POST", "/api/questions", 409, t.QuestionWriteFencedProblem),
    ProblemDeclaration("POST", "/api/questions/{question_id}/answers", 404, QuestionNotFoundProblem),
    ProblemDeclaration("POST", "/api/questions/{question_id}/answers", 409, t.QuestionClosedProblem),
    # Findings.
    ProblemDeclaration("GET", "/api/findings/{finding_id}", 404, t.FindingNotFoundProblem),
    *(
        ProblemDeclaration("POST", f"/api/findings/{verb}", status, model)
        for verb in _FINDING_VERBS
        for status, model in (
            (404, t.FindingNotFoundProblem),
            (409, t.FindingTransitionRefusedProblem),
            (409, t.FindingWriteContendedProblem),
            (422, t.FindingNoteRequiredProblem),
            (422, t.DuplicateFindingProblem),
        )
    ),
    ProblemDeclaration("POST", "/api/findings/supersede", 422, t.FindingSupersedesItselfProblem),
    ProblemDeclaration("POST", "/api/findings/supersede", 422, t.AbsorberNotLiveProblem),
    # Flows.
    *(
        ProblemDeclaration("GET", path, 404, t.FlowNotFoundProblem)
        for path in ("/api/flows/{flow_id}", "/api/flows/{flow_id}/record")
    ),
    ProblemDeclaration(
        "GET", "/api/chunks/{chunk_id}/flow-submissions/{submission_id}/turns", 404, t.SubmissionNotFoundProblem
    ),
)
