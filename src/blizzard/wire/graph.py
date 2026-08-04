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


class GraphPolicyRequest(BaseModel):
    """Set a graph's follow-latest policy — the tri-state (issue #164).

    ``follow_latest`` is required and all three values are meaningful: ``true``/``false``
    override the hub-level setting for chunks pinned to this mint, and explicit ``null``
    reverts to inheriting it. It carries no default, so clearing the override is asked
    for by naming ``null`` rather than done by an omitted field (pinned by
    tests/test_pin_wire.py::test_graph_policy_request_follow_latest_carries_no_default);
    ``by`` is recorded on the appended fact, exactly as retire/re-enable do."""

    follow_latest: bool | None
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


class RotatePolicyView(BaseModel):
    """One declared session's rotation bounds (issue #144).

    The wire counterpart of :class:`~blizzard.hub.domain.graph.RotatePolicy`. Every
    threshold is independently optional; ``max_invocations`` counts **harness
    invocations** (spawn, resume, judge, nudge), not node-steps — one node-step burns two
    or three of them."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class GraphSessionView(BaseModel):
    """One graph-level named session declaration (issue #144).

    The wire counterpart of :class:`~blizzard.hub.domain.graph.SessionDecl`. ``model`` is
    a prioritized preference list of opaque strings — a ``blizzard:`` tier alias or a
    harness-native name — resolved left-to-right by the runner's adapter at session mint;
    the hub interprets neither it nor ``effort``."""

    name: str
    model: list[str] = []
    effort: str | None = None
    rotate: RotatePolicyView | None = None


class GraphNodeView(BaseModel):
    """A reified node in a minted graph — the full immutable definition."""

    node_id: str
    name: str
    executor: str
    session: str
    # The session reference target (issues #115, #144) — see
    # ``blizzard.hub.domain.graph.Node.session_source``. ``None`` means "chunk
    # most-recent" (bare ``resume``) or bare ``fresh``; otherwise the declared session or
    # node name the ``resume:``/``fresh:`` reference targets, read together with ``session``.
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
    same fact spelled out explicitly, distinguishing "retired" from "merely superseded
    by a newer version" (:class:`GraphSummaryView`'s ``effective``). Two wire fields for
    one fact, not drift: the only constructor,
    :func:`~blizzard.hub.api.graphs._graph_view`, sets both from the same ``retired``
    bool in one call (pinned by
    tests/test_graph_lifecycle_api.py::test_retire_returns_202_and_the_view_reports_retired
    and ::test_a_freshly_minted_graph_reports_enabled_and_not_retired).

    ``follow_latest`` is the stored **tri-state** (issue #164), served as-is: ``true`` /
    ``false`` override the hub-level setting for chunks pinned to this mint, and ``null``
    — every mint's default — inherits it. Deliberately the stored value rather than the
    resolved one, so a reader can tell "this graph says nothing" from "this graph says
    false"; the resolution against `HubConfig.follow_latest` happens at the transition,
    where the hub setting is in hand."""

    graph_id: str
    name: str
    entry_node_id: str
    enabled: bool
    retired: bool = False
    follow_latest: bool | None = None
    # The graph-level named-session declarations (issue #144), in authored order — empty
    # for every graph that declares none, which is every graph minted before #144.
    sessions: list[GraphSessionView] = []
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
