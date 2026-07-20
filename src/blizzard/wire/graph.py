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


class GraphChoiceView(BaseModel):
    """One selectable outcome of a node's judgement."""

    choice_id: str
    name: str
    description: str


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
    judged_by: str
    retries_max: int | None = None
    retries_exhausted: str | None = None
    mode: str | None = None
    prompt: str | None = None
    checks: list[str] = []
    produces: list[str] = []
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
