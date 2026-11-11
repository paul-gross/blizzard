"""Ready-queue and backlog wire shapes: the entries, whole-order responses, and pages; the peek, replace, and
position requests; and the chunk group request and response."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel

from blizzard.foundation.claim_pass_reason import ClaimPassReason
from blizzard.foundation.queue_policy import QueueMatchPolicy
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.chunk import BlockedView, WorkRefModel
from blizzard.wire.problem import ProblemDetails
from blizzard.wire.runner import RunnerCapability
from blizzard.wire.urgency import UrgencyRef
from blizzard.wire.vocabulary import OPEN, ToleratedRequestVocabulary


class RepositoryTarget(WireModel):
    """One repository a chunk's project links, with the branch the chunk starts from and lands into. The forge host,
    owner and repo name are what a runner matches a worktree's origin against. ``inherited`` when the branch is the
    repository record's own rather than the project link's override."""

    repository_id: str
    repository_slug: str
    forge_host: str
    owner: str
    repo: str
    base_branch: str
    inherited: bool


class QueuePeekEntry(WireModel):
    """One ready chunk, in queue order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []
    # The chunk's blocked marking — see BlockedView.
    blocked: BlockedView | None = None
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None
    #: The urgency the chunk carries; ``None`` from a hub that predates it.
    urgency: UrgencyRef | None = None
    #: The chunk's repository targets, which the matched peek names; ``None`` on every other read and from a hub that
    #: predates them.
    repository_targets: list[RepositoryTarget] | None = None


class QueuePeekResponse(WireModel):
    """The ready queue's whole order, unpaginated."""

    entries: list[QueuePeekEntry] = []


# An alias, not the bare union: FastAPI adds every enum a field's union names to the document as a component, and a
# tolerated request field's schema is a plain string.
type PeekPolicy = QueueMatchPolicy | Unrecognized


class QueuePeekRequest(BaseModel):
    """The matched fleet peek's own request body — ``POST /api/fleet/queue/peek``. Carries
    the calling runner's capability snapshot and queue policy; never a ``runner_id``,
    since the matched verb answers for the authenticated principal alone. ``policy="hold"``
    stops at an unusable head; any other value, including an unrecognized one, is pass-over."""

    capabilities: list[RunnerCapability] = []
    policy: Annotated[PeekPolicy, ToleratedRequestVocabulary("Policy")] = QueueMatchPolicy.PASS_OVER
    #: Narrows the peek to these projects of the runner's declaration, each by the entry as declared or the id it
    #: resolved to, and — under ``policy="hold"`` — holds per project rather than across them. ``None`` peeks every
    #: project the runner serves; an empty list offers nothing.
    projects: list[str] | None = None
    #: The runner prepares environments at the repository targets the peek names. A peek without it is never offered a
    #: chunk whose targets include a project link's own branch.
    repository_targets: bool = False


class NextClaimEntry(WireModel):
    """One ready chunk a runner's walk reaches, at its place in claim order."""

    chunk_id: str
    graph_id: str
    #: The chunk's index in the whole ready queue's claim order.
    position: int
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None
    #: The urgency the chunk carries; ``None`` where it carries none.
    urgency: UrgencyRef | None = None


class NextClaimPassedEntry(NextClaimEntry):
    """One chunk the walk passed, with why; a blocked one names the first prerequisite of its marking and the count."""

    reason: Annotated[ClaimPassReason | Unrecognized, OPEN]
    prerequisite: str | None = None
    prerequisite_count: int = 0


class NextClaimResponse(WireModel):
    """``GET /api/runners/{runner_id}/next-claim`` — the hub's best account of what one runner reaches for next, walked
    from what the hub holds of it: ``next`` under pass-over with the chunks ``passed`` before it (at most twenty,
    ``passed_total`` counting all), ``if_holding`` under a per-project hold with the head each served project's hold
    stops at (``held_at``), and ``unserved``, the chunks ahead of ``next`` of projects the runner does not reach."""

    next: NextClaimEntry | None = None
    passed: list[NextClaimPassedEntry] = []
    passed_total: int = 0
    unserved: int = 0
    if_holding: NextClaimEntry | None = None
    held_at: list[NextClaimPassedEntry] = []


class QueuePageView(WireModel):
    """``GET /api/queue``'s own bounded page: ``next_cursor`` is
    ``None`` on the last page; ``position`` is each entry's whole-order index, not page-local."""

    entries: list[QueuePeekEntry] = []
    next_cursor: str | None = None


class QueueReplaceRequest(BaseModel):
    """Idempotent whole-order replacement of the ready queue — ``PUT /api/queue``.

    ``chunk_ids`` is the desired order, front to back; each must name a ready chunk
    (``409``) and not repeat (``422``). An unnamed ready chunk is appended, order kept."""

    chunk_ids: list[str]


class QueuePositionRequest(BaseModel):
    """Single-chunk fractional reposition — ``POST /api/queue/position``.

    ``after_chunk_id=null`` moves ``chunk_id`` to the top, otherwise immediately after
    the named chunk. Both must be ready (``409``); a self-anchor is ``422``."""

    chunk_id: str
    after_chunk_id: str | None


class BacklogPeekEntry(WireModel):
    """One ``not_ready`` chunk, in backlog order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[WorkRefModel] = []
    # The chunk's blocked marking — see BlockedView.
    blocked: BlockedView | None = None
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None
    #: The urgency the chunk carries; ``None`` from a hub that predates it.
    urgency: UrgencyRef | None = None


class BacklogPeekResponse(WireModel):
    """The ``not_ready`` list's whole order, unpaginated."""

    entries: list[BacklogPeekEntry] = []


class BacklogPageView(WireModel):
    """``GET /api/backlog``'s own bounded page: ``next_cursor``
    is ``None`` on the last page; ``position`` is each entry's whole-order index, not page-local."""

    entries: list[BacklogPeekEntry] = []
    next_cursor: str | None = None


class BacklogReplaceRequest(BaseModel):
    """Idempotent whole-order replacement of the backlog — ``PUT /api/backlog``.

    ``chunk_ids`` is the desired order, front to back; each must name a ``not_ready``
    chunk (``409``) and not repeat (``422``). An unnamed chunk is appended, order kept."""

    chunk_ids: list[str]


class BacklogPositionRequest(BaseModel):
    """Single-chunk fractional reposition — ``POST /api/backlog/position``.

    ``after_chunk_id=null`` moves ``chunk_id`` to the top, otherwise immediately after
    the named chunk. Both must be ``not_ready`` (``409``); a self-anchor is ``422``."""

    chunk_id: str
    after_chunk_id: str | None


class ChunkGroupRequest(BaseModel):
    """Merge unacquired chunks into one.

    ``merge_chunk_ids`` fold into the path's survivor, which absorbs the union of their
    work refs. Self-references and duplicates are ignored; a non-ready member is ``409``."""

    merge_chunk_ids: list[str]


class ChunkGroupResponse(WireModel):
    """The survivor chunk after a group — its id and the union of work refs it carries."""

    chunk_id: str
    work_refs: list[WorkRefModel] = []
    merged_chunk_ids: list[str] = []


NOT_IN_LIST: Final = "urn:blizzard:problem:not-in-list"


SELF_ANCHORED_MOVE: Final = "urn:blizzard:problem:self-anchored-move"


CONTRADICTORY_PLACEMENT: Final = "urn:blizzard:problem:contradictory-placement"


DUPLICATE_REORDER_IDS: Final = "urn:blizzard:problem:duplicate-reorder-ids"


class NotInListProblem(ProblemDetails[Literal["urn:blizzard:problem:not-in-list"]]):
    """A reorder named a chunk outside the list it ranks."""


class SelfAnchoredMoveProblem(ProblemDetails[Literal["urn:blizzard:problem:self-anchored-move"]]):
    """A single-chunk reorder named the chunk as its own anchor."""


class ContradictoryPlacementProblem(ProblemDetails[Literal["urn:blizzard:problem:contradictory-placement"]]):
    """A placement named both an anchor and the top."""


class DuplicateReorderIdsProblem(ProblemDetails[Literal["urn:blizzard:problem:duplicate-reorder-ids"]]):
    """A whole-order replacement named one chunk twice."""


QUEUE_PROBLEM_TYPES: frozenset[str] = frozenset(
    {NOT_IN_LIST, SELF_ANCHORED_MOVE, CONTRADICTORY_PLACEMENT, DUPLICATE_REORDER_IDS}
)
