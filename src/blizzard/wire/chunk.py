"""Chunk ingest, views, and the PM pass-through (D-047/D-004).

Ingest wraps one or more PM pointers into chunks (``POST /chunks``); a pointer
already held by a live chunk is rejected **409** with the existing chunk id (D-093).
The list/detail views carry the **derived** status (D-004) — never a stored column
— and the current node. ``GET /chunks/{id}/pm-item`` is the vendor-native
pass-through read (D-047), contents never stored.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.domain.work import ChunkStatus
from blizzard.wire.decision import DecisionView
from blizzard.wire.question import QuestionView


class PmPointerModel(BaseModel):
    """One ``{provider, url}`` PM pointer (D-075)."""

    provider: str
    url: str


class ChunkIngestRequest(BaseModel):
    """Ingest by pointer — specific items always, batch fine (D-047)."""

    pointers: list[PmPointerModel]


class ChunkIngestResponse(BaseModel):
    """The minted chunk id."""

    chunk_id: str


class ChunkIngestConflict(BaseModel):
    """The 409 body: the pointer is already held by a live chunk (D-093)."""

    existing_chunk_id: str
    provider: str
    url: str
    detail: str = "pointer already held by a live chunk"


class ChunkSummary(BaseModel):
    """One row of the fleet chunk list — derived status + current node (D-004)."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    pm_pointers: list[PmPointerModel] = []


class RouteView(BaseModel):
    """A chunk's route — where it is being worked (D-021)."""

    runner_id: str
    workspace_id: str
    environment_ids: list[str] = []


class EscalationView(BaseModel):
    """An open escalation on a ``needs_human`` chunk (D-009/D-067).

    Surfaces the runner-composed takeover command so a human can resume the parked
    session (design/harness-adapters.md). Present only while the escalation is open —
    a later lease mint (requeue/takeover) supersedes it and this drops away (D-067)."""

    epoch: int
    takeover_command: str


class TransitionView(BaseModel):
    """One accepted transition in a chunk's history (D-027/D-036).

    The edge a node-step took — its origin node, the judgement choice that routed it,
    and its destination — oldest first on the detail. This is what makes the review-fail
    loop legible: a ``review -> build`` entry with ``choice_name = "fail"`` is a visible
    step in the timeline (MVP criterion 9/11)."""

    from_node_id: str | None
    to_node_id: str
    choice_name: str | None
    epoch: int
    recorded_at: str


class ArtifactView(BaseModel):
    """One entry of a chunk's inline artifact store (D-036).

    ``key`` is the store key ``{node}.{artifact-name}.{epoch}`` — append-only, so
    every re-run's entry is retained and latest-by-epoch resolution is the reader's
    (D-089). ``content`` carries an **asset's** text verbatim (a review's findings
    document); the ``repo``/``branch_name``/``commit_hash`` trio carries a
    ``git_commit`` artifact's pinned reference (the hub stores the reference, never the
    code — D-012)."""

    key: str
    kind: str
    name: str
    node_id: str
    node_name: str
    epoch: int
    content: str | None = None
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None


class PrView(BaseModel):
    """An open PR a chunk is parked on in open-pr delivery mode (D-059/D-065)."""

    repo: str
    number: int
    url: str


class CheckDeliveryResponse(BaseModel):
    """The result of an on-demand ``POST /chunks/{id}/check-delivery`` (D-065)."""

    chunk_id: str
    status: ChunkStatus
    finalized: bool  # True iff this check terminated the delivery
    open_prs: int  # PRs still awaiting an external merge
    detail: str


class ChunkDetail(BaseModel):
    """The chunk aggregate in full (D-036) — the board's chunk view and envelope feed.

    Carries the chunk's **transition history** and its inline **artifact store** so the
    web app can render every node it visited, the review that failed once and looped
    back to build, and the artifacts — the branch pointers merged and the review notes
    (product/mvp.md, MVP criterion 9/11)."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    latest_epoch: int | None
    pm_pointers: list[PmPointerModel] = []
    route: RouteView | None = None
    escalation: EscalationView | None = None
    # The chunk's live gate decision — the open (waiting_on_human) or resolved-but-not-
    # yet-transitioned one (D-045). The board renders its buttons + artifacts; the
    # holding runner picks up a resolved decision and records the resolving transition.
    decision: DecisionView | None = None
    history: list[TransitionView] = []
    artifacts: list[ArtifactView] = []
    # The chunk's open questions ([ask-answer.md], MVP criterion 7): a ``waiting_on_human``
    # chunk carries the ask a human answers with ``blizzard hub answer``.
    questions: list[QuestionView] = []
    # Open-pr delivery (D-059/D-065): a ``delivering`` chunk whose deliver node opened a
    # PR instead of merging is parked awaiting an external merge. ``open_prs`` are the PRs
    # a human reviews and merges; ``check-delivery`` then drives the chunk to ``done``.
    awaiting_external_merge: bool = False
    open_prs: list[PrView] = []


class PmItemView(BaseModel):
    """A pass-through PM item read (D-047) — body + comments, vendor-native."""

    provider: str
    url: str
    fetched_at: str
    body: str
    comments: list[str] = []
