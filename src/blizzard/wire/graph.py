"""Graph wire shapes: the mint, policy, and flags requests; the validation report; the graph and its node, edge,
choice, session, and rotation views; the sync report; and the summary row."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.graph_lifecycle import GraphLifecycle
from blizzard.foundation.graph_targets import ChoiceTargetKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN, DeclaredClosed


class GraphMintRequest(BaseModel):
    """A graph definition to mint — the raw YAML body."""

    definition_yaml: str


class GraphPolicyRequest(BaseModel):
    """Set a graph's follow-latest policy — the tri-state.

    ``follow_latest`` is required and carries no default: ``true``/``false`` override the
    hub-level setting for this mint's chunks, explicit ``null`` reverts to inheriting it."""

    follow_latest: bool | None


class GraphFlagsPatchRequest(BaseModel):
    """A sparse edit of a graph's mutable flags: an absent field is unchanged, a present
    one is set. ``follow_latest`` is the tri-state, so an explicit ``null`` reverts to
    inheriting the hub default rather than being refused."""

    model_config = ConfigDict(extra="forbid")

    follow_latest: bool | None = None


class GraphValidationReport(BaseModel):
    """The validator's verdict — the 422 body when errors reject a mint."""

    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class ProducesEntry(WireModel):
    """One node's ``produces:`` expectation, kind-carrying."""

    name: str
    kind: Annotated[ArtifactKind, DeclaredClosed("the runner holds the node's produced artifacts to their kind")] = (
        ArtifactKind.ASSET
    )


class GraphChoiceView(WireModel):
    """One selectable outcome of a node's judgement."""

    choice_id: str
    name: str
    description: str
    # Whether this choice is gated on green checks; the default keeps a
    # regenerated client additive.
    requires_checks: bool = False


class GraphEdgeView(WireModel):
    """A directed, choice-keyed connection between two nodes of one graph."""

    from_node_id: str
    choice_id: str
    to_node_name: str
    prompt_addendum: str | None = None
    #: What ``to_node_name`` points at.
    target_kind: Annotated[ChoiceTargetKind | Unrecognized, OPEN] = ChoiceTargetKind.NODE
    #: The cross-graph target's graph name; ``None`` unless ``target_kind`` is ``graph``.
    target_graph: str | None = None


class RotatePolicyView(WireModel):
    """One declared session's rotation bounds.

    Every threshold is independently optional; ``max_invocations`` counts **harness
    invocations**, not node-steps, one of which burns several."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class GraphSessionView(WireModel):
    """One graph-level named session declaration.

    ``model`` is a preference list resolved left-to-right at mint; the hub interprets none
    of it, ``effort``, ``compaction_window``, or ``harnesses`` (its own acceptable set)."""

    name: str
    model: list[str] = []
    effort: str | None = None
    rotate: RotatePolicyView | None = None
    compaction_window: str | None = None
    harnesses: list[str] = []


class GraphNodeView(WireModel):
    """A reified node in a minted graph — the full immutable definition."""

    node_id: str
    name: str
    executor: Annotated[Executor | Unrecognized, OPEN]
    session: Annotated[SessionMode | Unrecognized, OPEN]
    # The session reference target: ``None`` for a bare reference,
    # otherwise the declared session or node name it targets. Read with ``session``.
    session_source: str | None = None
    judged_by: Annotated[JudgedBy | Unrecognized, OPEN]
    retries_max: int | None = None
    retries_exhausted: str | None = None
    prompt: str | None = None
    checks: list[str] = []
    # Where this node's checks run, and the per-check timeout.
    checks_cwd: str | None = None
    checks_timeout: int | None = None
    produces: list[ProducesEntry] = []
    judgement_prompt: str | None = None
    choices: list[GraphChoiceView] = []
    # The hub action a hub node names, and its ``with:`` arguments, each a list of values;
    # ``None`` and empty on a runner node or a hub node naming ``run:``.
    action: str | None = None
    action_args: dict[str, list[str]] = {}


class GraphView(WireModel):
    """A minted graph as served by ``GET /graphs/{graph_id}`` and the mint response.

    ``enabled`` and ``retired`` are one lifecycle fact, saying nothing about
    whether this mint is newest. ``follow_latest`` is the **stored** tri-state (#164)."""

    graph_id: str
    name: str
    entry_node_id: str
    enabled: bool
    retired: bool = False
    follow_latest: bool | None = None
    # The graph-level named-session declarations, in authored order.
    sessions: list[GraphSessionView] = []
    # The graph-scoped `artifacts:` declarations' names, in authored order — never their content.
    artifacts: list[str] = []
    nodes: list[GraphNodeView] = []
    edges: list[GraphEdgeView] = []
    warnings: list[str] = []
    #: The graph's source YAML exactly as minted, resubmittable as :attr:`GraphMintRequest.definition_yaml`.
    definition_yaml: str


class GraphSyncEntry(WireModel):
    """One packaged graph's reconciliation outcome.

    ``status`` is ``minted``, ``up-to-date``, or ``failed``; ``graph_id`` is present only on
    ``minted``, and ``detail`` says why a graph minted or what went wrong."""

    name: str
    status: str
    graph_id: str | None = None
    detail: str | None = None


class GraphSyncResponse(WireModel):
    """``POST /graphs/sync``'s report — one entry per packaged graph.

    ``ok`` is false iff any entry failed, so a caller gates on the field rather than
    re-deriving it. Always ``200``: a per-graph failure is data, not a transport error."""

    ok: bool
    entries: list[GraphSyncEntry] = []


class GraphSummaryView(WireModel):
    """One graph's summary row — a name-lineage entry as served by ``GET /graphs``.

    ``effective`` marks the newest **non-retired** graph of this ``name``, while
    ``retired`` is this graph's own state: a non-retired non-effective graph is superseded.
    ``lifecycle`` names that three-way standing, so a client never re-derives it."""

    graph_id: str
    name: str
    entry_node_id: str
    created_at: str
    effective: bool
    retired: bool = False
    lifecycle: Annotated[GraphLifecycle | Unrecognized, OPEN] = GraphLifecycle.EFFECTIVE
