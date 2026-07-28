"""Graph mint request and read views.

``POST /graphs`` takes a YAML definition, validates it (errors reject, warnings
flag), inlines every file reference, and mints an immutable graph.
The request carries the YAML text; the response is a :class:`GraphView`. An invalid
definition returns **422** with a :class:`GraphValidationReport`.

``GET /graphs`` lists every minted graph as a :class:`GraphSummaryView`, newest
first, with the newest non-retired graph of each ``name`` marked ``effective``.
``GET /graphs/{graph_id}`` serves the same :class:`GraphView` the mint response
returns — the full reified definition, including nodes, edges, choices, and
prompts.

``POST /graphs/{graph_id}/retire`` and ``POST /graphs/{graph_id}/enable`` flip a
graph's reversible retire brake (issue #101), both taking a :class:`GraphLifecycleRequest`
and returning the same :class:`GraphView`, its ``enabled``/``retired`` fields updated.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.domain.artifacts import ArtifactKind


class GraphMintRequest(BaseModel):
    """A graph definition to mint — the raw YAML body."""

    definition_yaml: str


class GraphLifecycleRequest(BaseModel):
    """Retire or re-enable a graph — records who flipped it (issue #101)."""

    by: str = "operator"


class GraphValidationReport(BaseModel):
    """The validator's verdict — the 422 body when errors reject a mint."""

    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class ProducesEntry(BaseModel):
    """One node's ``produces:`` expectation, kind-carrying (D1, issue #143).

    The wire counterpart of :class:`~blizzard.hub.domain.graph.ProducesSpec` — served
    on :class:`GraphNodeView` and (imported) on
    :class:`~blizzard.wire.envelope.NodeConfig`, so both the graph-read surface and the
    runner's per-step envelope carry the same kind-carrying shape."""

    name: str
    kind: ArtifactKind = ArtifactKind.ASSET


class GraphChoiceView(BaseModel):
    """One selectable outcome of a node's judgement."""

    choice_id: str
    name: str
    description: str
    # Whether this choice is gated on green checks (issue #114) — see
    # ``blizzard.hub.domain.graph.Choice.requires_checks``. Default `False` keeps the
    # regenerated OpenAPI/TS client additive.
    requires_checks: bool = False


class GraphEdgeView(BaseModel):
    """A directed, choice-keyed connection between two nodes of one graph."""

    from_node_id: str
    choice_id: str
    to_node_name: str
    prompt_addendum: str | None = None


class GraphNodeView(BaseModel):
    """A reified node in a minted graph — the full immutable definition."""

    node_id: str
    name: str
    executor: str
    session: str
    # The targeted-resume source node name (issue #115) — see
    # ``blizzard.hub.domain.graph.Node.session_source``. ``None`` means "chunk
    # most-recent" (bare ``resume``) or ``fresh``.
    session_source: str | None = None
    judged_by: str
    retries_max: int | None = None
    retries_exhausted: str | None = None
    mode: str | None = None
    prompt: str | None = None
    checks: list[str] = []
    # Where the runner runs this node's checks and the per-check timeout (issue #114) —
    # see ``blizzard.hub.domain.graph.Node.checks_cwd`` / ``checks_timeout``.
    checks_cwd: str | None = None
    checks_timeout: int | None = None
    produces: list[ProducesEntry] = []
    judgement_prompt: str | None = None
    choices: list[GraphChoiceView] = []


class GraphView(BaseModel):
    """A minted graph as served by ``GET /graphs/{graph_id}`` and the mint response.

    ``enabled`` is ``not retired`` — the graph's own lifecycle state (issue #101),
    independent of whether it is currently the newest of its name. ``retired`` is the
    same fact spelled out explicitly for a board that wants to distinguish "retired"
    from "merely superseded by a newer version" (:class:`GraphSummaryView`'s
    ``effective``). Deliberately two wire fields for one fact, not drift: the only
    constructor, :func:`~blizzard.hub.api.graphs._graph_view`, sets both from the same
    ``retired`` bool in one call (``enabled=not retired, retired=retired``) — there is
    no second call site that could set one and forget the other."""

    graph_id: str
    name: str
    entry_node_id: str
    enabled: bool
    retired: bool = False
    nodes: list[GraphNodeView] = []
    edges: list[GraphEdgeView] = []
    warnings: list[str] = []


class GraphSyncEntry(BaseModel):
    """One packaged graph's reconciliation outcome (issue #146).

    ``status`` is ``minted``, ``up-to-date``, or ``failed``. ``graph_id`` is the freshly
    minted graph's id, present only on ``minted``. ``detail`` says *why* a graph minted,
    or what went wrong when it failed."""

    name: str
    status: str
    graph_id: str | None = None
    detail: str | None = None


class GraphSyncResponse(BaseModel):
    """``POST /graphs/sync``'s report — one entry per packaged graph (issue #146).

    ``ok`` is false iff any entry failed, so a deploy can gate on the field rather than
    re-deriving the rule from the rows. Always ``200``: a per-graph failure is data in
    this report, not a transport error — the other graphs still reconciled, and the
    caller needs to see both halves."""

    ok: bool
    entries: list[GraphSyncEntry] = []


class GraphSummaryView(BaseModel):
    """One graph's summary row — a name-lineage entry as served by ``GET /graphs``.

    ``effective`` is the newest **non-retired** graph of this ``name`` (issue #101);
    ``retired`` is this graph's own lifecycle state, independent of ``effective`` — a
    retired graph is never effective, but a non-retired, non-effective graph is merely
    superseded by a newer version of the same name."""

    graph_id: str
    name: str
    entry_node_id: str
    created_at: str
    effective: bool
    retired: bool = False
