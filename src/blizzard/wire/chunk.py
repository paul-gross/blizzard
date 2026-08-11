"""Chunk ingest, views, and the work-item pass-through.

Ingest wraps source-native **tokens** into chunks — ``{name}:{ref}``, ``{name}#{ref}``, or the item's
own URL, each resolved against the configured work sources; an unclaimed token is a **422** naming it,
and a resolved pointer already held by a live chunk is a **409** carrying the existing chunk id. The
views carry the **derived** status, never a stored column; work-item contents are never stored."""

from __future__ import annotations

from pydantic import BaseModel, Field

from blizzard.hub.domain.work import ChunkStatus, MigrationMode
from blizzard.wire.decision import DecisionView
from blizzard.wire.question import QuestionView


class WorkRefModel(BaseModel):
    """One ``{source, ref}`` work ref — ``source`` names a configured
    ``[[work_source]]``; ``ref`` is that source's own item token."""

    source: str
    ref: str


class WorkRefView(BaseModel):
    """A pointer as the views carry it — the raw pair plus its legible label and browser URL, both
    resolved by the pointer's configured source binding. ``label`` is the legible ``{name}#{ref}``;
    ``web_url`` is its browser-openable address. Both null when no configured source names ``source``."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None


class ChunkIngestRequest(BaseModel):
    """Ingest by source-native token — specific items always, batch fine. Each token is resolved against
    the configured work sources' own grammar: ``{name}:{ref}``, ``{name}#{ref}``, or the item's own URL.
    Tokens only; no pre-resolved ``{source, ref}`` shape travels alongside them."""

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
    """A chunk's derived usage/cost total, summed over every recorded invocation (issue #59) — never a
    stored column. ``cost_partial`` carries the lower-bound + PARTIAL contract on ``cost_usd``."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool

    @classmethod
    def zero(cls) -> ChunkUsageTotalView:
        """The default for a construction site that never sets ``cost`` itself."""
        return cls(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=0.0,
            cost_partial=False,
        )


class ChunkUsageView(BaseModel):
    """One node-step's usage/cost telemetry (issue #59) — one harness invocation's tokens-by-class and
    cost, oldest first on ``ChunkDetail``. ``cost_usd`` is ``None`` exactly when no result envelope
    existed for this invocation — never fabricated."""

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
    """One row of the fleet chunk list — the derived status and current node. ``current_node_name`` is
    the node's human graph name beside the raw ``nd_`` ULID, null when unresolvable.
    ``runner_id``/``environment_count`` are **in-progress-only** (issue #140): a terminal chunk reads
    unrouted even while its route facts stand. ``completed_at`` is the terminal instant, else null."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    work_refs: list[WorkRefView] = []
    # The chunk's default model preference and effort (issue #144) — what a surface declaring neither
    # inherits. Empty/None is the minted state and means *express no preference*, not "unknown".
    default_model: list[str] = []
    default_effort: str | None = None
    runner_id: str | None = None
    # The count of environments the chunk's live route holds (issue #69) — 0 when unrouted; a grouped
    # chunk counts them all, so a per-runner sum does not undercount.
    environment_count: int = 0
    # The chunk's derived usage/cost total (issue #59) — see ChunkUsageTotalView.
    cost: ChunkUsageTotalView = Field(default_factory=ChunkUsageTotalView.zero)
    # The chunk's derived completion instant (issue #173) — null for every non-terminal status.
    completed_at: str | None = None


class RouteView(BaseModel):
    """A chunk's route — where it is being worked."""

    runner_id: str
    workspace_id: str
    environment_ids: list[str] = []


class EscalationView(BaseModel):
    """An open escalation on a ``needs_human`` chunk — the takeover command(s) for the parked session,
    present only while the escalation is open — a later lease mint, requeue, or completion supersedes it.
    ``wrapped_takeover_command`` is optional, empty when none was composed."""

    epoch: int
    takeover_command: str
    wrapped_takeover_command: str = ""


class TransitionView(BaseModel):
    """One accepted transition in a chunk's history: the edge a node-step took — origin node, the
    judgement choice that routed it, destination — oldest first on the detail.
    ``from_node_name``/``to_node_name`` are the nodes' human graph names, null when unresolvable.
    ``graph_id``/``graph_name`` name the graph this step happened in (issue #90), both null on old rows."""

    from_node_id: str | None
    from_node_name: str | None = None
    to_node_id: str
    to_node_name: str | None = None
    choice_name: str | None
    epoch: int
    recorded_at: str
    graph_id: str | None = None
    graph_name: str | None = None


class MigrationView(BaseModel):
    """One cross-graph migration step (issue #90): the chunk's attempt ended in ``from_graph`` and it
    re-queued at ``landed_node`` in ``to_graph`` — its own step, never a transition
    (``bzh:migration-not-transition``). ``model`` is the re-pinned model, null when the chunk kept its
    own. ``source`` says what moved it: ``authored-edge``, ``intent``, or ``follow-latest`` (#164)."""

    from_node_id: str | None
    from_node_name: str | None = None
    from_graph_id: str
    from_graph_name: str | None = None
    to_graph_id: str
    to_graph_name: str | None = None
    landed_node_id: str | None = None
    landed_node_name: str | None = None
    choice_name: str | None = None
    model: str | None = None
    source: str | None = None
    recorded_at: str


class IntendedMigrationView(BaseModel):
    """A chunk's standing migration intent (issue #124) — editable at any non-terminal status and
    consulted, never applied eagerly, at the chunk's next transition. ``graph_name`` is resolved
    server-side from the stored ``graph_id``, null when unresolvable. ``node_name`` is the ``forced``
    mode's landing target, null for ``auto``, whose landing is derived at consult time."""

    mode: MigrationMode
    graph_id: str
    graph_name: str | None = None
    node_name: str | None = None


class IntendedMigrationPatch(BaseModel):
    """The intended-migration value a ``ChunkPatchRequest`` carries (issue #124). ``to_graph`` is a
    graph id, or a name resolved server-side to the newest enabled graph of that name at request time —
    the resolved **id** is stored. ``node`` present selects ``forced``, absent selects ``auto``; there
    is no separate ``mode`` field, so "node supplied under auto" is unrepresentable."""

    to_graph: str
    node: str | None = None


class ArtifactView(BaseModel):
    """One entry of a chunk's inline artifact store. ``key`` is ``{node}.{artifact-name}.{epoch}`` —
    append-only, so latest-by-epoch resolution is the reader's. ``content`` carries an **asset**'s text
    verbatim; ``repo``/``branch_name``/``commit_hash`` carry a ``git_commit``'s pinned reference, never
    the code. ``branch_url`` is the branch's forge URL. ``recorded_at`` decodes the id's ULID stamp."""

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


class BounceView(BaseModel):
    """One recorded delivery kick-back (#64) — contention, not failure, and never itself a status.
    ``envelope`` is the raw JSON kick-back payload verbatim."""

    cause: str
    envelope: str
    recorded_at: str


class HubAdvanceResponse(BaseModel):
    """The result of one on-demand hub-advance (#65). A generic hub command node runs ``run:`` to
    completion, one call at a time, behind the fleet-wide serialization slot: ``ran=False`` means the
    slot was held by a different chunk and this call deferred without touching anything — not an
    error."""

    chunk_id: str
    status: ChunkStatus
    ran: bool
    outcome_choice: str | None = None
    to_node_name: str | None = None
    detail: str = ""


class PendingView(BaseModel):
    """A hub node's in-progress poll (#66) — whether a chunk parked at a hub node is about to run its
    first attempt or is already mid-poll, and when the next is due. Never itself a status."""

    node_name: str
    next_poll_at: str


class HubMarkerRequest(BaseModel):
    """The mid-run marker callback's body (#65)."""

    name: str
    content: str = ""


class HubMarkerResponse(BaseModel):
    """The recorded marker — ``recorded=False`` iff it already existed (idempotent)."""

    recorded: bool
    chunk_id: str
    name: str


class ChunkPauseRequest(BaseModel):
    """Set or clear a chunk's operator pause brake — records who flipped it (issue #46)."""

    by: str = "operator"


class ChunkStopRequest(BaseModel):
    """Terminally abandon a chunk — records who stopped it (issue #118)."""

    by: str = "operator"


class ChunkPatchRequest(BaseModel):
    """The multi-field ``PATCH /chunks/{id}`` body (issue #124) — every field independently optional,
    applied all-or-nothing. ``graph_id``/``model`` mean "leave unchanged" whether omitted or explicitly
    ``null``. ``intended_migration`` *is* nullable, so omitted ("leave unchanged") stays distinguishable
    from explicit ``null`` ("clear it") by the key's presence in the body, never by its value."""

    graph_id: str | None = None
    default_model: list[str] | None = None
    default_effort: str | None = None
    intended_migration: IntendedMigrationPatch | None = None


class ChunkPatchResponse(BaseModel):
    """The result of one ``PATCH /chunks/{id}`` (issues #124, #144) — the chunk's editable build
    properties after the edit, carried together since a PATCH can apply more than one at once."""

    chunk_id: str
    graph_id: str
    default_model: list[str] = []
    default_effort: str | None = None
    intended_migration: IntendedMigrationView | None = None


class PauseView(BaseModel):
    """An open pause on a chunk (issue #46) — who set it and when, present only while ``paused=True``
    is the newest pause fact. Carried independently of ``status``: PAUSED sits below the human-gated
    statuses in the derivation order, so this is the only carrier of a pause on a gated chunk."""

    by: str
    set_at: str


class ChunkDetail(BaseModel):
    """The chunk aggregate in full, carrying its **transition history** — every node it visited,
    including a review that failed and looped back — and its inline **artifact store**."""

    chunk_id: str
    graph_id: str
    # The pinned graph's name and mint instant (issue #102) — `None` together iff the graph could not
    # be resolved.
    graph_name: str | None = None
    graph_created_at: str | None = None
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    latest_epoch: int | None
    work_refs: list[WorkRefView] = []
    # The chunk's default model preference and effort — see :class:`ChunkSummary`.
    default_model: list[str] = []
    default_effort: str | None = None
    # The chunk's standing migration intent (issue #124) — non-None iff an `auto` or `forced` intent
    # is set. See IntendedMigrationView.
    intended_migration: IntendedMigrationView | None = None
    route: RouteView | None = None
    escalation: EscalationView | None = None
    # The operator's per-chunk pause brake (issue #46) — non-None iff currently paused, and carried
    # independently of ``status`` so a gated-and-paused chunk stays legible (see PauseView).
    pause: PauseView | None = None
    # The chunk's live gate decision — the open (waiting_on_human) or resolved-but-not-
    # yet-transitioned one.
    decision: DecisionView | None = None
    history: list[TransitionView] = []
    # The chunk's cross-graph migration steps (issue #90), oldest first — woven into the timeline
    # alongside ``history`` by ``recorded_at``. Empty for a single-graph chunk.
    migrations: list[MigrationView] = []
    artifacts: list[ArtifactView] = []
    # The chunk's questions, oldest first — open *and* answered (issue #165), an answered one still
    # carrying its return trail.
    questions: list[QuestionView] = []
    # Open-pr delivery, kept for back-compat reads of a historical chunk (#67). No engine path writes
    # these facts.
    awaiting_external_merge: bool = False
    open_prs: list[PrView] = []
    # The chunk's derived usage/cost total (issue #59) — see ChunkUsageTotalView.
    cost: ChunkUsageTotalView = Field(default_factory=ChunkUsageTotalView.zero)
    # Per-node-step usage history, oldest first.
    usage: list[ChunkUsageView] = []
    # A hub command node's in-progress poll (#66) — non-None iff the newest transition enters a hub
    # node and a poll fact is recorded for that visit with no later transition. Never a status.
    pending: PendingView | None = None
    # Informational, never a status (#63): true iff any repo has landed, whether or not delivery has
    # reached its terminal transition — "merged but stuck" reads honestly rather than hiding.
    landed: bool = False
    # The chunk's recorded delivery kick-backs (#64), oldest first — informational, never a status:
    # a bounce is contention, not failure.
    bounces: list[BounceView] = []


# Pydantic's default ``extra="ignore"`` lets this validate straight off a ``ChunkDetail``
# payload, which keeps ``EscalationView`` out of the runner's own OpenAPI schema.
class ChunkHeaderView(BaseModel):
    """A chunk-detail header aggregate (issue #185) — identity, work-item links, live state, and the
    pause fact, without the transition/artifact history the hub's own chunk aggregate carries."""

    chunk_id: str
    status: ChunkStatus
    work_refs: list[WorkRefView] = []
    pause: PauseView | None = None


class WorkItemEntry(BaseModel):
    """One pointer's pass-through work item — title, body, and comment thread, vendor-native.
    ``label``/``web_url`` are the legible pointer label and its browser address, both null when no
    configured source names ``source``. A per-pointer failure degrades here rather than failing the
    whole read: ``error`` carries the reason and ``title``/``body`` are null."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None
    fetched_at: str
    title: str | None = None
    body: str | None = None
    comments: list[str] = []
    error: str | None = None


class WorkItemsView(BaseModel):
    """A chunk's pass-through work items — one entry per pointer, order preserved.

    Empty when the chunk holds no pointers; a grouped chunk carrying many pointers
    yields one entry per pointer, each fetched fresh and never stored."""

    items: list[WorkItemEntry] = []
