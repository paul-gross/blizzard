"""Graph reification and the mint service.

The mint pipeline's domain half: a validated :class:`GraphDoc` is compiled into an
immutable, id-carrying :class:`Graph` (:func:`reify_graph`) and persisted through
the write graph repository (:class:`GraphMintService`). Parsing YAML text and
inlining prompt *file* references are edge concerns done before this runs
(``bzh:domain-core``): the service sees an already-parsed doc plus the raw YAML it
stores verbatim for audit/re-export.

Validation errors reject the mint (:class:`GraphValidationError`, surfaced 422 at
the edge); warnings ride along on the minted graph.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import CHOICE_PREFIX, GRAPH_PREFIX, NODE_PREFIX, mint
from blizzard.hub.domain.graph import (
    RESERVED_TERMINAL,
    Choice,
    Edge,
    Graph,
    GraphDoc,
    IWriteGraphRepository,
    JudgedBy,
    Node,
)
from blizzard.hub.domain.graph_validation import ValidationResult, validate_graph


class GraphValidationError(Exception):
    """A graph definition failed mint-time validation — the 422 carrier."""

    def __init__(self, result: ValidationResult) -> None:
        super().__init__("; ".join(result.errors) or "graph validation failed")
        self.result = result


def reify_graph(doc: GraphDoc, clock: IClock) -> Graph:
    """Compile a validated authoring doc into an immutable, id-carrying graph.

    Ids are minted here — one graph id, a node id per node, a choice id per choice —
    and the fused choice/edge entries split into reified :class:`Choice` objects on
    the node and directed :class:`Edge` objects keyed by choice id. A hub node that
    omits its judgement carries no choices and no edges; its machinery outcomes are
    applied by the coordinator, not stored as edges.
    """
    graph_id = mint(GRAPH_PREFIX, clock)
    node_ids = {node.name: mint(NODE_PREFIX, clock) for node in doc.nodes}
    created_at = clock.now()

    nodes: list[Node] = []
    edges: list[Edge] = []
    for nd in doc.nodes:
        judged_by = nd.judgement.by if nd.judgement is not None else JudgedBy.WORKER
        choices: list[Choice] = []
        for choice_doc in nd.judgement.choices if nd.judgement is not None else []:
            choice_id = mint(CHOICE_PREFIX, clock)
            choices.append(Choice(choice_id=choice_id, name=choice_doc.name, description=choice_doc.description or ""))
            edges.append(
                Edge(
                    from_node_id=node_ids[nd.name],
                    choice_id=choice_id,
                    to_node_name=choice_doc.to or RESERVED_TERMINAL,
                    prompt_addendum=choice_doc.prompt_addendum,
                )
            )
        nodes.append(
            Node(
                node_id=node_ids[nd.name],
                graph_id=graph_id,
                name=nd.name,
                executor=nd.executor,
                prompt=nd.prompt,
                checks=list(nd.checks),
                produces=list(nd.produces),
                session=nd.session,
                judged_by=judged_by,
                retries_max=nd.retries_max,
                retries_exhausted=nd.retries_exhausted,
                mode=nd.mode,
                judgement_prompt=nd.judgement.prompt if nd.judgement is not None else None,
                choices=choices,
            )
        )
    return Graph(
        graph_id=graph_id,
        name=doc.name,
        entry_node_id=node_ids[doc.entry],
        nodes=nodes,
        edges=edges,
        created_at=created_at,
    )


class GraphMintService:
    """Validate, reify, and persist a graph — the ``POST /graphs`` domain rule.

    Holds the *write* graph repository (``bzh:controller-read-only``); the route
    resolves the YAML into a :class:`GraphDoc` and delegates here. Returns the minted
    :class:`Graph` and its validation warnings; raises :class:`GraphValidationError`
    on errors so the mint never persists an invalid definition.
    """

    def __init__(self, *, graphs: IWriteGraphRepository, clock: IClock) -> None:
        self._graphs = graphs
        self._clock = clock

    def mint(self, doc: GraphDoc, *, definition_yaml: str) -> tuple[Graph, list[str]]:
        result = validate_graph(doc)
        if not result.ok:
            raise GraphValidationError(result)
        graph = reify_graph(doc, self._clock)
        self._graphs.mint(graph, definition_yaml=definition_yaml, at=graph.created_at)
        return graph, result.warnings

    def ensure_default(self, doc: GraphDoc, *, definition_yaml: str) -> Graph:
        """Mint the configured default graph if no enabled graph of its name exists.

        Idempotent by name: a fresh hub mints the packaged default on first use, and
        re-checks are no-ops. Graphs stay immutable — this never edits an existing one.
        """
        existing = self._graphs.get_enabled_by_name(doc.name)
        if existing is not None:
            return existing
        graph, _ = self.mint(doc, definition_yaml=definition_yaml)
        return graph
