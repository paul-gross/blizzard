"""Chunk ingest, views, and the work-item pass-through.

Ingest wraps one or more source-native **tokens** into chunks (``POST /chunks``)
— ``{name}:{ref}``, ``{name}#{ref}``, or the item's own URL; the hub resolves
each against its configured work sources (``IWorkSourceRegistry.resolve``) and 422s a
token none of them claims, naming the token and the configured sources. A
resolved pointer already held by a live chunk is rejected **409** with the existing
chunk id. The list/detail views carry the **derived** status — never
a stored column — and the current node. ``GET /chunks/{id}/work-items`` is the
vendor-native pass-through read — one entry per pointer, contents never
stored.
"""

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
    """A pointer as the views carry it — the raw pair plus its legible label and browser
    URL, both resolved by the pointer's configured source binding.

    ``label`` is the legible ``{name}#{ref}`` (e.g. ``blizzard#8``); ``web_url``
    is its browser-openable address. Both null when no configured source names
    ``source``."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None


class ChunkIngestRequest(BaseModel):
    """Ingest by source-native token — specific items always, batch fine.

    Each token is resolved against the configured work sources' own grammar
    (``IWorkSource.parse``): ``{name}:{ref}``, ``{name}#{ref}``, or the item's own URL.
    Tokens only — no pre-resolved ``{source, ref}`` shape travels alongside them, since a
    second intake shape reintroduces the config-blind guess resolving removes (pinned by
    tests/test_pin_wire.py::test_chunk_ingest_accepts_source_native_tokens_only)."""

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
    """The all-zero, non-partial total — the default for a construction site that never
    sets ``cost`` itself."""
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

    ``current_node_name`` is the node's human graph name (``build``, ``review``) beside
    the raw ``nd_`` ULID; null when the chunk has no current node or its pinned graph
    cannot resolve the id.

    Status-only: the summary feeds the board **card**, a passive status view (issue #42),
    so it carries no operator *fact* — those reach the detail dock through
    :class:`ChunkDetail`. ``runner_id`` and ``environment_count`` (issue #69) are passive
    where-is-it facts, not action keys, and ``cost``/``completed_at`` (issues #59, #173)
    are cheap derived instants that ride along rather than waiting for the detail fetch.

    ``runner_id``/``environment_count`` are **in-progress-only** (issue #140): a chunk at
    a terminal status reads unrouted even while its route facts still show a route, so a
    per-runner fold counts live occupancy with no status filter of its own (pinned by
    tests/test_route_claim.py::test_summary_reports_a_finished_chunk_as_unrouted). The
    unfiltered route fact lives on :class:`ChunkDetail.route`.

    ``completed_at`` (issue #173) is the terminal instant — see
    :func:`~blizzard.hub.domain.work.derive_completed_at` — null for every non-terminal
    status. Like every wire instant it is a ``str``, populated via
    :func:`~blizzard.foundation.clock.iso_utc` at the serialization edge, never a bare
    ``datetime`` (``bzh:utc-instants``)."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    work_refs: list[WorkRefView] = []
    # The chunk's default model preference and effort (issue #144) — what a surface
    # declaring neither inherits; effective precedence is session declaration > chunk
    # default > runner default. Editable while `not_ready` or `ready`-and-unclaimed
    # (issue #120), the window #27's retired `model` field carried. Empty/None is the
    # minted state and means *express no preference*, not "unknown".
    default_model: list[str] = []
    default_effort: str | None = None
    runner_id: str | None = None
    # The count of environments the chunk's live route holds (issue #69) — 0 when
    # unrouted. A grouped chunk holding >1 environment counts them all, so a per-runner
    # sum does not undercount.
    environment_count: int = 0
    # The chunk's derived usage/cost total (issue #59) — see ChunkUsageTotalView.
    cost: ChunkUsageTotalView = Field(default_factory=_zero_usage_total)
    # The chunk's derived completion instant (issue #173) — null for every non-terminal
    # status. See derive_completed_at.
    completed_at: str | None = None


class RouteView(BaseModel):
    """A chunk's route — where it is being worked."""

    runner_id: str
    workspace_id: str
    environment_ids: list[str] = []


class EscalationView(BaseModel):
    """An open escalation on a ``needs_human`` chunk — the takeover command(s) so a
    human can resume the parked session. Present only while the escalation is open —
    a later lease mint (requeue/takeover) supersedes it and this drops away.

    ``wrapped_takeover_command`` is the blizzard-runner-wrapped equivalent of
    ``takeover_command``, empty when none was composed. Wrapped implies raw, never the
    reverse, and whether a takeover is actually possible for this escalation is a
    separate question from whether either is populated — see
    https://github.com/paul-gross/blizzard-context/blob/master/domain/humans.md for
    the full account."""

    epoch: int
    takeover_command: str
    wrapped_takeover_command: str = ""


class TransitionView(BaseModel):
    """One accepted transition in a chunk's history.

    The edge a node-step took — its origin node, the judgement choice that routed it,
    and its destination — oldest first on the detail. This is what makes the review-fail
    loop legible: a ``review -> build`` entry with ``choice_name = "fail"`` is a visible
    step in the timeline (MVP criterion 9/11).

    ``from_node_name``/``to_node_name`` are the nodes' human graph names (``build``,
    ``review``) beside the raw ``nd_`` ULIDs; resolved here so the timeline is legible
    without reassembly, null when the pinned graph cannot resolve the id.

    ``graph_id``/``graph_name`` identify the graph this step happened in (issue #90) —
    resolved per-transition against its own graph, so a chunk that later migrated still
    labels its old-graph steps with the graph they belong to rather than the current pin;
    both null for a step predating graph-provenance (never backfilled with a name)."""

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
    """One cross-graph migration step in a chunk's history (issue #90).

    A judgement choice targeting another graph ends the chunk's attempt in ``from_graph``
    and re-queues it at ``landed_node`` in ``to_graph`` — its own step in the timeline,
    never a transition (``bzh:migration-not-transition``). Node and
    graph names are resolved server-side against each side's own graph (null when
    unresolvable); ``model`` is the re-pinned model, or null when the chunk kept its own.

    ``source`` (issue #164) says **what** moved the chunk — ``authored-edge`` (a #90
    judgement choice), ``intent`` (an operator's #124 standing intent), or
    ``follow-latest`` (the standing policy). It is the only one of the three a human did
    not ask for, so this is how a reader learns why a chunk sits on a graph it did not
    start on. Null on a migration recorded before the discriminator existed — unrecorded,
    not defaulted, since a legacy row's two operator sources are indistinguishable."""

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
    """A chunk's standing migration intent (issue #124) — editable at any non-terminal
    status, ``not_ready``/``ready`` included, and consulted (never applied eagerly) at
    the chunk's next transition through the common apply path. Present on
    :class:`ChunkDetail`; ``None`` when no intent is set.

    ``graph_name`` is resolved server-side from the stored ``graph_id`` the same way
    :class:`MigrationView`'s ``to_graph_name`` is (null when the target graph cannot be
    resolved). ``node_name`` is the ``forced`` mode's unconditional landing target;
    null for ``auto`` (the landing name is derived at consult time from the
    transition's own destination, never carried here)."""

    mode: MigrationMode
    graph_id: str
    graph_name: str | None = None
    node_name: str | None = None


class IntendedMigrationPatch(BaseModel):
    """The intended-migration value a :class:`ChunkPatchRequest` carries (issue #124).

    ``to_graph`` names the migration target — a graph id, or a graph name resolved
    server-side to the newest enabled graph of that name at request time (the resolved
    **id** is what gets stored, so a later mint under the same name never silently
    re-aims a pending intent). ``node`` present selects ``forced`` (the unconditional
    landing target); absent selects ``auto`` — this shape carries no separate ``mode``
    field, so "node supplied under auto" is unrepresentable rather than a 422 the
    controller must catch."""

    to_graph: str
    node: str | None = None


class ArtifactView(BaseModel):
    """One entry of a chunk's inline artifact store.

    ``key`` is the store key ``{node}.{artifact-name}.{epoch}`` — append-only, so
    every re-run's entry is retained and latest-by-epoch resolution is the reader's.
    ``content`` carries an **asset's** text verbatim (a review's findings
    document); the ``repo``/``branch_name``/``commit_hash`` trio carries a
    ``git_commit`` artifact's pinned reference (the hub stores the reference, never the
    code).

    ``branch_url`` is the forge ``tree`` URL for the produced branch, resolved server-side
    from the chunk's issue-shaped work ref; null when no forge web base is derivable.

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


class BounceView(BaseModel):
    """One recorded delivery kick-back (#64) — contention, not failure.

    Surfaced on chunk detail so the bounce history is readable, without itself being (or
    affecting) the chunk's derived status. ``envelope`` is the raw JSON kick-back payload
    (cause detail, etc.) verbatim."""

    cause: str
    envelope: str
    recorded_at: str


class HubAdvanceResponse(BaseModel):
    """The result of one on-demand ``POST /api/fleet/chunks/{id}/hub-advance`` (#65,
    moved under the fleet router by #87).

    A generic hub command node runs ``run:`` to completion, one call at a time,
    behind the fleet-wide serialization slot: ``ran=False`` means the slot was held
    by a different chunk and this call deferred without touching anything — not an
    error, just try again on a later poll."""

    chunk_id: str
    status: ChunkStatus
    ran: bool
    outcome_choice: str | None = None
    to_node_name: str | None = None
    detail: str = ""


class PendingView(BaseModel):
    """A hub node's in-progress poll (#66) — waiting on external state, honestly.

    Surfaced on chunk detail so a ``delivering`` chunk parked at a hub node reads
    truthfully whether it is about to run its first attempt or already mid-poll, and
    when the next attempt is due — never itself a status (the chunk still derives
    ``delivering``, mirroring ``awaiting_external_merge``)."""

    node_name: str
    next_poll_at: str


class HubMarkerRequest(BaseModel):
    """The mid-run marker callback's body (#65) — mirrors ``blizzard runner ask``'s
    own worker-facing callback shape."""

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
    """The multi-field ``PATCH /chunks/{id}`` body (issue #124, in #104's shape) — every
    field independently optional, applied all-or-nothing by ``EditService.edit``.

    ``graph_id``/``model`` mean "leave unchanged" whether omitted or sent explicit
    ``null`` — neither is a nullable chunk property, so there is no "clear" state to
    distinguish. ``intended_migration`` *is* nullable, so **omitted** ("leave unchanged")
    must stay distinguishable from **explicit ``null``** ("clear it"); a plain
    ``Optional`` default cannot, so the controller keys on
    ``"intended_migration" in request.model_fields_set``, not this field's value (pinned
    by tests/test_chunk_edit_api.py::test_patch_clears_an_intended_migration_via_explicit_null
    and ::test_patch_with_intended_migration_field_absent_leaves_it_unchanged)."""

    graph_id: str | None = None
    default_model: list[str] | None = None
    default_effort: str | None = None
    intended_migration: IntendedMigrationPatch | None = None


class ChunkPatchResponse(BaseModel):
    """The result of one ``PATCH /chunks/{id}`` (issues #124, #144) — the chunk's
    editable build properties after the edit, carried together since a PATCH can apply
    more than one at once."""

    chunk_id: str
    graph_id: str
    default_model: list[str] = []
    default_effort: str | None = None
    intended_migration: IntendedMigrationView | None = None


class PauseView(BaseModel):
    """An open pause on a chunk (issue #46) — who set it and when.

    Present only while ``paused=True`` is the newest pause fact; a resume clears it.
    Carried independently of ``status``: PAUSED sits below the human-gated statuses in
    the derivation order, so a chunk both paused and parked on a question derives
    ``waiting_on_human`` — this field is then the only carrier of the pause fact, and it
    also answers "who paused it"."""

    by: str
    set_at: str


class ChunkDetail(BaseModel):
    """The chunk aggregate in full.

    Carries the chunk's **transition history** — every node it visited, including a
    review that failed and looped back to build — and its inline **artifact store**."""

    chunk_id: str
    graph_id: str
    # The pinned graph's name and mint instant (issue #102) — populated from the
    # already-loaded `Graph` at detail assembly, no extra store read. `None` together
    # iff the graph could not be resolved.
    graph_name: str | None = None
    graph_created_at: str | None = None
    status: ChunkStatus
    current_node_id: str | None
    current_node_name: str | None = None
    latest_epoch: int | None
    work_refs: list[WorkRefView] = []
    # The chunk's default model preference and effort (issue #144) — what a surface
    # declaring neither inherits; effective precedence is session declaration > chunk
    # default > runner default. Editable while `not_ready` or `ready`-and-unclaimed
    # (issue #120), the window #27's retired `model` field carried. Empty/None is the
    # minted state and means *express no preference*, not "unknown".
    default_model: list[str] = []
    default_effort: str | None = None
    # The chunk's standing migration intent (issue #124) — non-None iff an `auto` or
    # `forced` intent is set, consulted (never applied eagerly) at the chunk's next
    # transition. See IntendedMigrationView.
    intended_migration: IntendedMigrationView | None = None
    route: RouteView | None = None
    escalation: EscalationView | None = None
    # The operator's per-chunk pause brake (issue #46) — non-None iff currently paused.
    # Carried independently of ``status``: PAUSED sits below the human-gated statuses, so
    # a chunk both paused and waiting_on_human needs this field to be legible as paused
    # at all (see PauseView). The runner reads this fact, not the derived status.
    pause: PauseView | None = None
    # The chunk's live gate decision — the open (waiting_on_human) or resolved-but-not-
    # yet-transitioned one.
    decision: DecisionView | None = None
    history: list[TransitionView] = []
    # The chunk's cross-graph migration steps (issue #90), oldest first — woven into the
    # timeline alongside ``history`` by ``recorded_at``. Empty for the common single-graph
    # chunk; a migration re-pins the chunk and re-queues it under another graph.
    migrations: list[MigrationView] = []
    artifacts: list[ArtifactView] = []
    # The chunk's questions, oldest first — open *and* answered (issue #165). An
    # already-answered one stays here carrying its return trail: who answered, and
    # whether the answer has been delivered into the resumed session.
    questions: list[QuestionView] = []
    # Open-pr delivery, kept for back-compat reads of a historical chunk (#67): a
    # ``delivering`` chunk parked awaiting an external merge, with ``open_prs`` naming
    # the PRs a human reviewed and merged. No engine path writes these facts.
    awaiting_external_merge: bool = False
    open_prs: list[PrView] = []
    # The chunk's derived usage/cost total (issue #59) — see ChunkUsageTotalView.
    cost: ChunkUsageTotalView = Field(default_factory=_zero_usage_total)
    # Per-node-step usage history, oldest first.
    usage: list[ChunkUsageView] = []
    # A generic hub command node's in-progress poll (#66) — non-None iff the chunk's
    # newest transition enters a hub node AND a poll fact is recorded for that visit
    # with no later transition. Never a status: the chunk still derives ``delivering``.
    pending: PendingView | None = None
    # Informational, never a status (#63): true iff any repo has landed for this chunk,
    # whether or not delivery has reached the terminal transition yet — an authored
    # ``merged -> <node>`` edge can hold the chunk running (or escalated) in a
    # post-merge node with every repo already merged. "Merged but stuck" reads
    # honestly here rather than un-merging or hiding behind `status`.
    landed: bool = False
    # The chunk's recorded delivery kick-backs (#64), oldest first — informational,
    # never a status: a bounce is contention, not failure, and this reads truthfully
    # even once the count has crossed the node's cap and the chunk derives needs_human.
    bounces: list[BounceView] = []


class ChunkHeaderView(BaseModel):
    """The chunk-detail dock's header aggregate (issue #185) — the identity,
    work-item links, live state, and pause fact a header needs, projected down
    from :class:`ChunkDetail` rather than carrying its transition/artifact
    history. Pydantic's default ``extra="ignore"`` lets it validate straight off a
    `ChunkDetail` payload, so the runner's chunk-detail proxy never pulls this
    module's :class:`EscalationView` into the runner's OpenAPI schema, where it
    would collide with ``wire.runner_status``' identically-named view (pinned by
    tests/test_pin_wire.py::test_the_runner_spec_escalation_view_is_the_runners_own).

    ``pause`` is carried independently of ``status`` for the same reason
    :class:`ChunkDetail`'s own field is: a chunk both paused and parked on a
    question still derives ``waiting_on_human``."""

    chunk_id: str
    status: ChunkStatus
    work_refs: list[WorkRefView] = []
    pause: PauseView | None = None


class WorkItemEntry(BaseModel):
    """One pointer's pass-through work item — title, body + comment
    thread, vendor-native.

    ``label``/``web_url`` are the legible pointer label (``blizzard#8``) and its
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


class WorkItemsView(BaseModel):
    """A chunk's pass-through work items — one entry per pointer, order preserved.

    Empty when the chunk holds no pointers; a grouped chunk carrying many pointers
    yields one entry per pointer, each fetched fresh and never stored."""

    items: list[WorkItemEntry] = []
