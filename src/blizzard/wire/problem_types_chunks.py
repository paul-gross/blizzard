"""The chunk-family refusals: problem types, wire variants, and the set the integrator registers."""

from typing import Final, Literal

from blizzard.wire.problem import ProblemDetails

INGEST_TOKEN_UNCLAIMED: Final = "urn:blizzard:problem:ingest-token-unclaimed"
INGEST_EMPTY: Final = "urn:blizzard:problem:ingest-empty"
INGEST_ITEM_PROJECT_MISMATCH: Final = "urn:blizzard:problem:ingest-item-project-mismatch"
CHUNK_NOT_ESCALATED: Final = "urn:blizzard:problem:chunk-not-escalated"
CHUNK_NOT_RESTARTABLE: Final = "urn:blizzard:problem:chunk-not-restartable"
NODE_NOT_ON_GRAPH: Final = "urn:blizzard:problem:node-not-on-graph"
CURRENT_NODE_NOT_ON_GRAPH: Final = "urn:blizzard:problem:current-node-not-on-graph"
RESTART_GRAPH_PIN_CHANGED: Final = "urn:blizzard:problem:restart-graph-pin-changed"
TARGET_GRAPH_RETIRED: Final = "urn:blizzard:problem:target-graph-retired"
MIGRATION_TARGET_IS_CURRENT_PIN: Final = "urn:blizzard:problem:migration-target-is-current-pin"
CHUNK_NOT_ROUTED: Final = "urn:blizzard:problem:chunk-not-routed"
CHUNK_NOT_PROMOTABLE: Final = "urn:blizzard:problem:chunk-not-promotable"
CHUNK_NOT_DEMOTABLE: Final = "urn:blizzard:problem:chunk-not-demotable"
CHUNK_NOT_STOPPABLE: Final = "urn:blizzard:problem:chunk-not-stoppable"
CHUNK_NOT_DELETABLE: Final = "urn:blizzard:problem:chunk-not-deletable"
CHUNK_HAS_DEPENDENTS: Final = "urn:blizzard:problem:chunk-has-dependents"
CHUNK_NOT_EDITABLE: Final = "urn:blizzard:problem:chunk-not-editable"
CHUNK_ALREADY_MOVED: Final = "urn:blizzard:problem:chunk-already-moved"
NOT_IN_LIST: Final = "urn:blizzard:problem:not-in-list"
SELF_ANCHORED_MOVE: Final = "urn:blizzard:problem:self-anchored-move"
CONTRADICTORY_PLACEMENT: Final = "urn:blizzard:problem:contradictory-placement"
DUPLICATE_REORDER_IDS: Final = "urn:blizzard:problem:duplicate-reorder-ids"
CHUNK_NOT_GROUPABLE: Final = "urn:blizzard:problem:chunk-not-groupable"
CHUNKS_IN_DIFFERENT_PROJECTS: Final = "urn:blizzard:problem:chunks-in-different-projects"
FOLD_WOULD_CLOSE_CYCLE: Final = "urn:blizzard:problem:fold-would-close-cycle"
BLANK_EDIT_VALUE: Final = "urn:blizzard:problem:blank-edit-value"
INVALID_HARNESSES: Final = "urn:blizzard:problem:invalid-harnesses"
DECISION_NOT_FOUND: Final = "urn:blizzard:problem:decision-not-found"
NOT_A_DECISION_CHOICE: Final = "urn:blizzard:problem:not-a-decision-choice"
DECISION_CLOSED: Final = "urn:blizzard:problem:decision-closed"
QUESTION_WRITE_FENCED: Final = "urn:blizzard:problem:question-write-fenced"
QUESTION_CLOSED: Final = "urn:blizzard:problem:question-closed"
FINDING_NOT_FOUND: Final = "urn:blizzard:problem:finding-not-found"
FINDING_TRANSITION_REFUSED: Final = "urn:blizzard:problem:finding-transition-refused"
FINDING_WRITE_CONTENDED: Final = "urn:blizzard:problem:finding-write-contended"
FINDING_NOTE_REQUIRED: Final = "urn:blizzard:problem:finding-note-required"
DUPLICATE_FINDING: Final = "urn:blizzard:problem:duplicate-finding"
FINDING_SUPERSEDES_ITSELF: Final = "urn:blizzard:problem:finding-supersedes-itself"
ABSORBER_NOT_LIVE: Final = "urn:blizzard:problem:absorber-not-live"
SUBMISSION_NOT_FOUND: Final = "urn:blizzard:problem:submission-not-found"
FLOW_NOT_FOUND: Final = "urn:blizzard:problem:flow-not-found"

CHUNKS_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        INGEST_TOKEN_UNCLAIMED,
        INGEST_EMPTY,
        INGEST_ITEM_PROJECT_MISMATCH,
        CHUNK_NOT_ESCALATED,
        CHUNK_NOT_RESTARTABLE,
        NODE_NOT_ON_GRAPH,
        CURRENT_NODE_NOT_ON_GRAPH,
        RESTART_GRAPH_PIN_CHANGED,
        TARGET_GRAPH_RETIRED,
        MIGRATION_TARGET_IS_CURRENT_PIN,
        CHUNK_NOT_ROUTED,
        CHUNK_NOT_PROMOTABLE,
        CHUNK_NOT_DEMOTABLE,
        CHUNK_NOT_STOPPABLE,
        CHUNK_NOT_DELETABLE,
        CHUNK_HAS_DEPENDENTS,
        CHUNK_NOT_EDITABLE,
        CHUNK_ALREADY_MOVED,
        NOT_IN_LIST,
        SELF_ANCHORED_MOVE,
        CONTRADICTORY_PLACEMENT,
        DUPLICATE_REORDER_IDS,
        CHUNK_NOT_GROUPABLE,
        CHUNKS_IN_DIFFERENT_PROJECTS,
        FOLD_WOULD_CLOSE_CYCLE,
        BLANK_EDIT_VALUE,
        INVALID_HARNESSES,
        DECISION_NOT_FOUND,
        NOT_A_DECISION_CHOICE,
        DECISION_CLOSED,
        QUESTION_WRITE_FENCED,
        QUESTION_CLOSED,
        FINDING_NOT_FOUND,
        FINDING_TRANSITION_REFUSED,
        FINDING_WRITE_CONTENDED,
        FINDING_NOTE_REQUIRED,
        DUPLICATE_FINDING,
        FINDING_SUPERSEDES_ITSELF,
        ABSORBER_NOT_LIVE,
        SUBMISSION_NOT_FOUND,
        FLOW_NOT_FOUND,
    }
)


class IngestTokenUnclaimedProblem(ProblemDetails[Literal["urn:blizzard:problem:ingest-token-unclaimed"]]):
    """An ingest token no configured work source claims."""


class IngestEmptyProblem(ProblemDetails[Literal["urn:blizzard:problem:ingest-empty"]]):
    """An ingest named no work ref."""


class IngestItemProjectMismatchProblem(ProblemDetails[Literal["urn:blizzard:problem:ingest-item-project-mismatch"]]):
    """A hub item the ingest wraps records a project other than the one the ingest mints into."""


class ChunkNotEscalatedProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-escalated"]]):
    """A requeue targeted a chunk that is not waiting on a human."""


class ChunkNotRestartableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-restartable"]]):
    """A restart targeted a terminal chunk."""


class NodeNotOnGraphProblem(ProblemDetails[Literal["urn:blizzard:problem:node-not-on-graph"]]):
    """A restart or forced migration named a node its landing graph does not carry."""


class CurrentNodeNotOnGraphProblem(ProblemDetails[Literal["urn:blizzard:problem:current-node-not-on-graph"]]):
    """The chunk stands on a node its own pinned graph does not carry."""


class RestartGraphPinChangedProblem(ProblemDetails[Literal["urn:blizzard:problem:restart-graph-pin-changed"]]):
    """A concurrent edit re-pinned the chunk between the caller's load and the restart."""


class TargetGraphRetiredProblem(ProblemDetails[Literal["urn:blizzard:problem:target-graph-retired"]]):
    """A graph named explicitly as a new target has been retired."""


class MigrationTargetIsCurrentPinProblem(
    ProblemDetails[Literal["urn:blizzard:problem:migration-target-is-current-pin"]]
):
    """An intended migration's target graph is the chunk's own current pin."""


class ChunkNotRoutedProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-routed"]]):
    """A detach targeted a chunk with no live, claimed route."""


class ChunkNotPromotableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-promotable"]]):
    """A promote targeted a never-promoted chunk outside the promotable window."""


class ChunkNotDemotableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-demotable"]]):
    """A demote targeted a chunk that is not resting in the ready lane."""


class ChunkNotStoppableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-stoppable"]]):
    """A stop targeted a chunk that is already done or stopped."""


class ChunkNotDeletableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-deletable"]]):
    """A delete targeted a chunk outside the pre-claim window."""


class ChunkHasDependentsProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-has-dependents"]]):
    """A delete targeted a chunk that is a standing prerequisite for other chunks."""


class ChunkNotEditableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-editable"]]):
    """An edit supplied a field outside that field's editable window."""


class ChunkAlreadyMovedProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-already-moved"]]):
    """A graph re-pin named a chunk that has already moved."""


class NotInListProblem(ProblemDetails[Literal["urn:blizzard:problem:not-in-list"]]):
    """A reorder named a chunk outside the list it ranks."""


class SelfAnchoredMoveProblem(ProblemDetails[Literal["urn:blizzard:problem:self-anchored-move"]]):
    """A single-chunk reorder named the chunk as its own anchor."""


class ContradictoryPlacementProblem(ProblemDetails[Literal["urn:blizzard:problem:contradictory-placement"]]):
    """A placement named both an anchor and the top."""


class DuplicateReorderIdsProblem(ProblemDetails[Literal["urn:blizzard:problem:duplicate-reorder-ids"]]):
    """A whole-order replacement named one chunk twice."""


class ChunkNotGroupableProblem(ProblemDetails[Literal["urn:blizzard:problem:chunk-not-groupable"]]):
    """A group named a chunk outside the pre-claim window."""


class ChunksInDifferentProjectsProblem(ProblemDetails[Literal["urn:blizzard:problem:chunks-in-different-projects"]]):
    """A group named a chunk of another project than the survivor's."""


class FoldWouldCloseCycleProblem(ProblemDetails[Literal["urn:blizzard:problem:fold-would-close-cycle"]]):
    """A group would close a cycle in the standing dependency graph."""


class BlankEditValueProblem(ProblemDetails[Literal["urn:blizzard:problem:blank-edit-value"]]):
    """An edit supplied a value that is blank once trimmed."""


class InvalidHarnessesProblem(ProblemDetails[Literal["urn:blizzard:problem:invalid-harnesses"]]):
    """A harness preference contains a blank or a duplicate."""


class DecisionNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:decision-not-found"]]):
    """The named decision does not exist in the tenant."""


class NotADecisionChoiceProblem(ProblemDetails[Literal["urn:blizzard:problem:not-a-decision-choice"]]):
    """A resolution named a choice the gate does not offer."""


class DecisionClosedProblem(ProblemDetails[Literal["urn:blizzard:problem:decision-closed"]]):
    """A resolution reached a gate something already closed undecided."""


class QuestionWriteFencedProblem(ProblemDetails[Literal["urn:blizzard:problem:question-write-fenced"]]):
    """The write fence refused a question.asked row."""


class QuestionClosedProblem(ProblemDetails[Literal["urn:blizzard:problem:question-closed"]]):
    """An answer reached a question whose chunk has ended."""


class FindingNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:finding-not-found"]]):
    """The named finding does not exist in the tenant."""


class FindingTransitionRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:finding-transition-refused"]]):
    """A finding verb was applied to a finding whose state does not allow it."""


class FindingWriteContendedProblem(ProblemDetails[Literal["urn:blizzard:problem:finding-write-contended"]]):
    """A finding verb's write kept losing to concurrent writes."""


class FindingNoteRequiredProblem(ProblemDetails[Literal["urn:blizzard:problem:finding-note-required"]]):
    """A finding verb carried a blank or missing note."""


class DuplicateFindingProblem(ProblemDetails[Literal["urn:blizzard:problem:duplicate-finding"]]):
    """A bulk finding verb named the same finding twice."""


class FindingSupersedesItselfProblem(ProblemDetails[Literal["urn:blizzard:problem:finding-supersedes-itself"]]):
    """A supersede named its own finding as the absorber."""


class AbsorberNotLiveProblem(ProblemDetails[Literal["urn:blizzard:problem:absorber-not-live"]]):
    """A supersede named an absorber that is not itself live."""


class SubmissionNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:submission-not-found"]]):
    """The named flow submission does not belong to the chunk."""


class FlowNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:flow-not-found"]]):
    """The named flow does not exist in the tenant."""
