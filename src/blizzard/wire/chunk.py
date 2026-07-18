"""Chunk ingest, views, and the PM pass-through.

Ingest wraps one or more source-native **tokens** into chunks (``POST /chunks``)
— ``{name}:{ref}``, ``{name}#{ref}``, or the item's own URL; the hub resolves
each against its configured PM sources (``IPmSourceRegistry.resolve``) and 422s a
token none of them claims, naming the token and the configured sources. A
resolved pointer already held by a live chunk is rejected **409** with the existing
chunk id. The list/detail views carry the **derived** status — never
a stored column — and the current node. ``GET /chunks/{id}/pm-items`` is the
vendor-native pass-through read — one entry per pointer, contents never
stored.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from blizzard.hub.domain.work import ChunkStatus
from blizzard.wire.decision import DecisionView
from blizzard.wire.question import QuestionView


class PmPointerModel(BaseModel):
    """One ``{source, ref}`` PM pointer — ``source`` names a configured
    ``[[pm_source]]``; ``ref`` is that source's own item token."""

    source: str
    ref: str


class PmPointerView(BaseModel):
    """A pointer as the views render it — the raw pair plus its legible
    label and browser URL, both rendered by the pointer's configured source binding.

    ``label`` is the board-legible ``{name}#{ref}`` (e.g. ``blizzard#8``); ``web_url``
    is its browser-openable address. Both null when no configured source names
    ``source`` — the board then leans on the chunk's stable short id instead."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None


class ChunkIngestRequest(BaseModel):
    """Ingest by source-native token — specific items always, batch fine.

    Each token is resolved against the configured PM sources' own grammar
    (``IPmSource.parse``): ``{name}:{ref}``, ``{name}#{ref}``, or the item's own URL.
    Tokens only — no pre-resolved ``{source, ref}`` shape travels alongside them; a
    second intake shape would reintroduce exactly the config-blind guess that
    resolving against the configured sources removes."""

    tokens: list[str]


class ChunkIngestResponse(BaseModel):
    """The minted chunk id."""

    chunk_id: str


class ChunkIngestConflict(BaseModel):
    """The 409 body: the pointer is already held by a live chunk."""

    existing_chunk_id: str
    source: str
    ref: str
    detail: str = "pointer already held by a live chunk"


class ChunkUsageTotalView(BaseModel):
    """A chunk's derived usage/cost total — summed over every recorded invocation
    (issue #59). Never a stored column: computed at read time by
    ``derive_chunk_usage``.

    ``cost_partial`` carries the lower-bound + PARTIAL contract on ``cost_usd`` —
    see :class:`~blizzard.hub.domain.work.UsageTotal` for the one canonical
    statement of it, which this view's fields mirror verbatim."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


def _zero_usage_total() -> ChunkUsageTotalView:
    """The all-zero, non-partial total — the default for a construction site (mostly
    fakes in the runner-side test suite) that predates usage telemetry and never sets
    ``cost`` itself; the real hub API always populates it from ``derive_chunk_usage``."""
    return ChunkUsageTotalView(
        input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_create_tokens=0, cost_usd=0.0, cost_partial=False
    )


class ChunkUsageView(BaseModel):
    """One node-step's usage/cost telemetry (issue #59) — one harness invocation's
    tokens-by-class and cost, oldest first on :class:`ChunkDetail`.

    ``cost_usd`` is ``None`` exactly when no result envelope existed for this
    invocation (the envelope-less transcript-summation fallback) — never fabricated."""

    node_id: str
    epoch: int
    kind: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None


class ChunkSummary(BaseModel):
    """One row of the fleet chunk list — derived status + current node.

    ``current_node_name`` is the node's human graph name (``build``, ``review``) the
    board renders in place of the raw ``nd_`` ULID; null when the chunk has no
    current node or its pinned graph cannot resolve the id.

    Deliberately status-only: the summary feeds the board **card**, which is a passive
    status view (issue #42), so no operator *fact* is carried here. The pause fact — and
    every other fact an operator action keys on — reaches the chunk detail dock through
    :class:`ChunkDetail`, the one place a board action lives. ``runner_id`` (the live
    route's holder, null when unrouted) is a passive where-is-it fact in that same
    sense — it lets the fleet registry list each runner's claims — not an action key.
    ``cost`` is the one exception (issue #59): the derived spend total is cheap to carry
    on every card and is not itself an operator fact, so it rides the summary rather than
    waiting for the detail fetch."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    pm_pointers: list[PmPointerView] = []
    # The chunk's model selection (issue #27) — editable while `not_ready`. Required:
    # the store column is non-nullable and every mint sets DEFAULT_MODEL.
    model: str
    runner_id: str | None = None
    # The chunk's derived usage/cost total (issue #59) — see ChunkUsageTotalView.
    cost: ChunkUsageTotalView = Field(default_factory=_zero_usage_total)


class RouteView(BaseModel):
    """A chunk's route — where it is being worked."""

    runner_id: str
    workspace_id: str
    environment_ids: list[str] = []


class EscalationView(BaseModel):
    """An open escalation on a ``needs_human`` chunk.

    Surfaces the runner-composed takeover command so a human can resume the parked
    session. Present only while the escalation is open —
    a later lease mint (requeue/takeover) supersedes it and this drops away."""

    epoch: int
    takeover_command: str


class TransitionView(BaseModel):
    """One accepted transition in a chunk's history.

    The edge a node-step took — its origin node, the judgement choice that routed it,
    and its destination — oldest first on the detail. This is what makes the review-fail
    loop legible: a ``review -> build`` entry with ``choice_name = "fail"`` is a visible
    step in the timeline (MVP criterion 9/11).

    ``from_node_name``/``to_node_name`` are the nodes' human graph names (``build``,
    ``review``) the board renders in place of the raw ``nd_`` ULIDs; resolved here so the
    timeline is legible without reassembly, null when the pinned graph cannot
    resolve the id."""

    from_node_id: str | None
    from_node_name: str | None = None
    to_node_id: str
    to_node_name: str | None = None
    choice_name: str | None
    epoch: int
    recorded_at: str


class ArtifactView(BaseModel):
    """One entry of a chunk's inline artifact store.

    ``key`` is the store key ``{node}.{artifact-name}.{epoch}`` — append-only, so
    every re-run's entry is retained and latest-by-epoch resolution is the reader's.
    ``content`` carries an **asset's** text verbatim (a review's findings
    document); the ``repo``/``branch_name``/``commit_hash`` trio carries a
    ``git_commit`` artifact's pinned reference (the hub stores the reference, never the
    code).

    ``branch_url`` is the forge ``tree`` URL for the produced branch, resolved server-side
    from the chunk's issue-shaped PM pointer so the board can link a ``git_commit``
    to the branch on the forge; null when no forge web base is derivable — the row then
    shows the branch name without a link (no broken link).

    ``recorded_at`` is the instant the artifact was attached, decoded from its id's
    ULID timestamp (the store keeps no separate column); null for a malformed id."""

    key: str
    kind: str
    name: str
    node_id: str
    node_name: str
    epoch: int
    recorded_at: str | None = None
    content: str | None = None
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    branch_url: str | None = None


class PrView(BaseModel):
    """An open PR a chunk is parked on in open-pr delivery mode."""

    repo: str
    number: int
    url: str


class CheckDeliveryResponse(BaseModel):
    """The result of an on-demand ``POST /chunks/{id}/check-delivery``."""

    chunk_id: str
    status: ChunkStatus
    finalized: bool  # True iff this check terminated the delivery
    open_prs: int  # PRs still awaiting an external merge
    detail: str


class ChunkPauseRequest(BaseModel):
    """Set or clear a chunk's operator pause brake — records who flipped it (issue #46)."""

    by: str = "operator"


class ChunkGraphUpdateRequest(BaseModel):
    """Repin a not-ready chunk's workflow graph (issue #27) — the target graph's id."""

    graph_id: str


class ChunkGraphView(BaseModel):
    """A chunk's current graph selection — the read/write shape issue #27's board editor uses."""

    chunk_id: str
    graph_id: str


class ChunkModelUpdateRequest(BaseModel):
    """Repin a not-ready chunk's model selection (issue #27)."""

    model: str


class ChunkModelView(BaseModel):
    """A chunk's current model selection — the read/write shape issue #27's board editor uses."""

    chunk_id: str
    model: str


class PauseView(BaseModel):
    """An open pause on a chunk (issue #46) — who set it and when.

    Present only while ``paused=True`` is the newest pause fact; a resume clears it.
    Carried independently of ``status``: PAUSED sits below the human-gated statuses in
    the derivation order, so a chunk both paused and parked on a question derives
    ``waiting_on_human`` — this field is the only way the runner (and the board) learn
    the chunk is paused in that case, and it also answers "who paused it"."""

    by: str
    set_at: str


class ChunkDetail(BaseModel):
    """The chunk aggregate in full — the board's chunk view and envelope feed.

    Carries the chunk's **transition history** and its inline **artifact store** so the
    web app can render every node it visited, the review that failed once and looped
    back to build, and the artifacts — the branch pointers merged and the review notes."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    latest_epoch: int | None
    pm_pointers: list[PmPointerView] = []
    # The chunk's model selection (issue #27) — editable while `not_ready`. Required:
    # the store column is non-nullable and every mint sets DEFAULT_MODEL.
    model: str
    route: RouteView | None = None
    escalation: EscalationView | None = None
    # The operator's per-chunk pause brake (issue #46) — non-None iff currently paused.
    # Carried independently of ``status``: PAUSED sits below the human-gated statuses, so
    # a chunk both paused and waiting_on_human needs this field to be legible as paused
    # at all (see PauseView). The runner reads this fact, not the derived status.
    pause: PauseView | None = None
    # The chunk's live gate decision — the open (waiting_on_human) or resolved-but-not-
    # yet-transitioned one. The board renders its buttons + artifacts; the
    # holding runner picks up a resolved decision and records the resolving transition.
    decision: DecisionView | None = None
    history: list[TransitionView] = []
    artifacts: list[ArtifactView] = []
    # The chunk's open questions: a ``waiting_on_human``
    # chunk carries the ask a human answers with ``blizzard hub answer``.
    questions: list[QuestionView] = []
    # Open-pr delivery: a ``delivering`` chunk whose deliver node opened a
    # PR instead of merging is parked awaiting an external merge. ``open_prs`` are the PRs
    # a human reviews and merges; ``check-delivery`` then drives the chunk to ``done``.
    awaiting_external_merge: bool = False
    open_prs: list[PrView] = []
    # The chunk's derived usage/cost total (issue #59) — see ChunkUsageTotalView.
    cost: ChunkUsageTotalView = Field(default_factory=_zero_usage_total)
    # Per-node-step usage history, oldest first — the board's future cost timeline.
    usage: list[ChunkUsageView] = []


class PmItemEntry(BaseModel):
    """One pointer's pass-through PM item — title, body + comment
    thread, vendor-native.

    ``label``/``web_url`` are the board-legible pointer label (``blizzard#8``) and its
    browser address — both null when no configured source names ``source``. A
    per-pointer forge failure degrades here rather than failing the whole read:
    ``error`` carries the reason and ``title``/``body`` are null, so one unreachable
    pointer never blinds the reader to the pointers it did reach."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None
    fetched_at: str
    title: str | None = None
    body: str | None = None
    comments: list[str] = []
    error: str | None = None


class PmItemsView(BaseModel):
    """A chunk's pass-through PM items — one entry per pointer, order preserved.

    Empty when the chunk holds no pointers — the board's empty state; a grouped chunk carrying
    many pointers yields one entry per pointer, each fetched fresh and never stored."""

    items: list[PmItemEntry] = []
