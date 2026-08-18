"""Graph mint request and read views.

A mint takes a YAML definition and answers with a :class:`GraphView` — the full reified
definition — or **422** with a :class:`GraphValidationReport`. The list surface serves a
:class:`GraphSummaryView` per minted graph, newest first, with the newest non-retired graph of
each ``name`` marked ``effective``; the lifecycle verbs return an updated view (issue #101)."""

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

    ``follow_latest`` is required and carries no default: ``true``/``false`` override the
    hub-level setting for this mint's chunks, explicit ``null`` reverts to inheriting it."""

    follow_latest: bool | None
    by: str = "operator"


class GraphValidationReport(BaseModel):
    """The validator's verdict — the 422 body when errors reject a mint."""

    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class ProducesEntry(BaseModel):
    """One node's ``produces:`` expectation, kind-carrying (D1, issue #143)."""

    name: str
    kind: ArtifactKind = ArtifactKind.ASSET


class GraphChoiceView(BaseModel):
    """One selectable outcome of a node's judgement."""

    choice_id: str
    name: str
    description: str
    # Whether this choice is gated on green checks (issue #114); the default keeps a
    # regenerated client additive.
    requires_checks: bool = False


class GraphEdgeView(BaseModel):
    """A directed, choice-keyed connection between two nodes of one graph."""

    from_node_id: str
    choice_id: str
    to_node_name: str
    prompt_addendum: str | None = None


class RotatePolicyView(BaseModel):
    """One declared session's rotation bounds (issue #144).

    Every threshold is independently optional; ``max_invocations`` counts **harness
    invocations**, not node-steps, one of which burns several."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class GraphSessionView(BaseModel):
    """One graph-level named session declaration (issue #144).

    ``model`` is a prioritized preference list of opaque strings, resolved left-to-right at
    session mint; the hub interprets neither it nor ``effort``."""

    name: str
    model: list[str] = []
    effort: str | None = None
    rotate: RotatePolicyView | None = None
    compaction_window: str | None = None


class GraphNodeView(BaseModel):
    """A reified node in a minted graph — the full immutable definition."""

    node_id: str
    name: str
    executor: str
    session: str
    # The session reference target (issues #115, #144): ``None`` for a bare reference,
    # otherwise the declared session or node name it targets. Read with ``session``.
    session_source: str | None = None
    judged_by: str
    retries_max: int | None = None
    retries_exhausted: str | None = None
    mode: str | None = None
    prompt: str | None = None
    checks: list[str] = []
    # Where this node's checks run, and the per-check timeout (issue #114).
    checks_cwd: str | None = None
    checks_timeout: int | None = None
    produces: list[ProducesEntry] = []
    judgement_prompt: str | None = None
    choices: list[GraphChoiceView] = []


class GraphView(BaseModel):
    """A minted graph as served by ``GET /graphs/{graph_id}`` and the mint response.

    ``enabled`` and ``retired`` are one lifecycle fact (issue #101), saying nothing about
    whether this mint is newest. ``follow_latest`` is the **stored** tri-state (#164)."""

    graph_id: str
    name: str
    entry_node_id: str
    enabled: bool
    retired: bool = False
    follow_latest: bool | None = None
    # The graph-level named-session declarations (issue #144), in authored order.
    sessions: list[GraphSessionView] = []
    # The graph-scoped `artifacts:` declarations' names, in authored order — content is not
    # served here; a worker reads it through the fleet protocol under a lease.
    artifacts: list[str] = []
    nodes: list[GraphNodeView] = []
    edges: list[GraphEdgeView] = []
    warnings: list[str] = []


class GraphSyncEntry(BaseModel):
    """One packaged graph's reconciliation outcome (issue #146).

    ``status`` is ``minted``, ``up-to-date``, or ``failed``; ``graph_id`` is present only on
    ``minted``, and ``detail`` says why a graph minted or what went wrong."""

    name: str
    status: str
    graph_id: str | None = None
    detail: str | None = None


class GraphSyncResponse(BaseModel):
    """``POST /graphs/sync``'s report — one entry per packaged graph (issue #146).

    ``ok`` is false iff any entry failed, so a caller gates on the field rather than
    re-deriving it. Always ``200``: a per-graph failure is data, not a transport error."""

    ok: bool
    entries: list[GraphSyncEntry] = []


class GraphSummaryView(BaseModel):
    """One graph's summary row — a name-lineage entry as served by ``GET /graphs``.

    ``effective`` marks the newest **non-retired** graph of this ``name`` (issue #101), while
    ``retired`` is this graph's own state: a non-retired non-effective graph is superseded."""

    graph_id: str
    name: str
    entry_node_id: str
    created_at: str
    effective: bool
    retired: bool = False
