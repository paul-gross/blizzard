"""Chunk ingest, views, and the PM pass-through (D-047/D-004).

Ingest wraps one or more PM pointers into chunks (``POST /chunks``); a pointer
already held by a live chunk is rejected **409** with the existing chunk id (D-093).
The list/detail views carry the **derived** status (D-004) — never a stored column
— and the current node. ``GET /chunks/{id}/pm-items`` is the vendor-native
pass-through read (D-047/D-084) — one entry per pointer, contents never stored.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.domain.work import ChunkStatus
from blizzard.wire.decision import DecisionView
from blizzard.wire.question import QuestionView


class PmPointerModel(BaseModel):
    """One ``{source, ref}`` PM pointer (D-105) — ``source`` names a configured
    ``[[pm_source]]``; ``ref`` is that source's own item token."""

    source: str
    ref: str


class PmPointerView(BaseModel):
    """A pointer as the views render it (D-105/D-108) — the raw pair plus its legible
    label and browser URL, both rendered by the pointer's configured source binding.

    ``label`` is the board-legible ``{name}#{ref}`` (e.g. ``blizzard#8``); ``web_url``
    is its browser-openable address. Both null when no configured source names
    ``source`` — the board then leans on the chunk's stable short id instead."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None


class ChunkIngestRequest(BaseModel):
    """Ingest by pointer — specific items always, batch fine (D-047)."""

    pointers: list[PmPointerModel]


class ChunkIngestResponse(BaseModel):
    """The minted chunk id."""

    chunk_id: str


class ChunkIngestConflict(BaseModel):
    """The 409 body: the pointer is already held by a live chunk (D-093)."""

    existing_chunk_id: str
    source: str
    ref: str
    detail: str = "pointer already held by a live chunk"


class ChunkSummary(BaseModel):
    """One row of the fleet chunk list — derived status + current node (D-004).

    ``current_node_name`` is the node's human graph name (``build``, ``review``) the
    board renders in place of the raw ``nd_`` ULID; null when the chunk has no
    current node or its pinned graph cannot resolve the id."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    pm_pointers: list[PmPointerView] = []


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
    step in the timeline (MVP criterion 9/11).

    ``from_node_name``/``to_node_name`` are the nodes' human graph names (``build``,
    ``review``) the board renders in place of the raw ``nd_`` ULIDs; resolved here so the
    timeline is legible without reassembly (D-075), null when the pinned graph cannot
    resolve the id."""

    from_node_id: str | None
    from_node_name: str | None = None
    to_node_id: str
    to_node_name: str | None = None
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
    code — D-012).

    ``branch_url`` is the forge ``tree`` URL for the produced branch, resolved server-side
    from the chunk's issue-shaped PM pointer (D-075) so the board can link a ``git_commit``
    to the branch on the forge; null when no forge web base is derivable — the row then
    shows the branch name without a link (no broken link)."""

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
    branch_url: str | None = None


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
    current_node_name: str | None = None
    latest_epoch: int | None
    pm_pointers: list[PmPointerView] = []
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


class PmItemEntry(BaseModel):
    """One pointer's pass-through PM item (D-047/D-074/D-105) — body + comment thread,
    vendor-native.

    ``label``/``web_url`` are the board-legible pointer label (``blizzard#8``) and its
    browser address (D-108) — both null when no configured source names ``source``. A
    per-pointer forge failure degrades here rather than failing the whole read (D-084):
    ``error`` carries the reason and ``body`` is null, so one unreachable pointer never
    blinds the reader to the pointers it did reach."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None
    fetched_at: str
    body: str | None = None
    comments: list[str] = []
    error: str | None = None


class PmItemsView(BaseModel):
    """A chunk's pass-through PM items (D-074/D-084) — one entry per pointer, order preserved.

    Empty when the chunk holds no pointers — the board's empty state; a grouped chunk carrying
    many pointers (D-047) yields one entry per pointer, each fetched fresh and never stored."""

    items: list[PmItemEntry] = []
