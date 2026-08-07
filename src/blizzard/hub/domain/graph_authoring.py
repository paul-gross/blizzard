"""Graph reification and the mint service.

A validated :class:`GraphDoc` is compiled into an immutable, id-carrying
:class:`Graph` (:func:`reify_graph`) and persisted (:class:`GraphMintService`); the
raw YAML is stored verbatim for audit and re-export. Validation errors reject the
mint (:class:`GraphValidationError`); warnings ride along on the minted graph."""

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
    RunStep,
)
from blizzard.hub.domain.graph_validation import ValidationResult, Validator


class GraphValidationError(Exception):
    """A graph definition failed mint-time validation — the 422 carrier."""

    def __init__(self, result: ValidationResult) -> None:
        super().__init__("; ".join(result.errors) or "graph validation failed")
        self.result = result


class DefaultGraphRetired(Exception):
    """Every minted graph named ``name`` is retired (issue #101).

    Distinct from "never minted", which still mints; this refuses to silently re-mint
    over an operator's deliberate brake."""

    def __init__(self, name: str) -> None:
        super().__init__(f"every graph named {name!r} is retired — re-enable one or mint a new one before ingesting")
        self.name = name


def reify_graph(doc: GraphDoc, clock: IClock) -> Graph:
    """Compile a validated authoring doc into an immutable, id-carrying graph.

    Ids are minted here — one graph id, a node id per node, a choice id per choice —
    and the fused choice/edge entries split into reified :class:`Choice` objects on the
    node and directed :class:`Edge` objects keyed by choice id."""
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
            choices.append(
                Choice(
                    choice_id=choice_id,
                    name=choice_doc.name,
                    description=choice_doc.description or "",
                    requires_checks=choice_doc.requires_checks,
                )
            )
            edges.append(
                Edge(
                    from_node_id=node_ids[nd.name],
                    choice_id=choice_id,
                    to_node_name=choice_doc.to or RESERVED_TERMINAL,
                    prompt_addendum=choice_doc.prompt_addendum,
                    target_graph=choice_doc.target_graph,
                    model=choice_doc.model,
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
                session_source=nd.session_source,
                judged_by=judged_by,
                retries_max=nd.retries_max,
                retries_exhausted=nd.retries_exhausted,
                mode=nd.mode,
                judgement_prompt=nd.judgement.prompt if nd.judgement is not None else None,
                choices=choices,
                bounce_cap=nd.bounce_cap,
                run=[RunStep(command=r.command, name=r.name, produces=r.produces) for r in nd.run],
                poll_interval_seconds=nd.poll_interval_seconds,
                poll_timeout_seconds=nd.poll_timeout_seconds,
                checks_cwd=nd.checks_cwd,
                checks_timeout=nd.checks_timeout,
            )
        )
    return Graph(
        graph_id=graph_id,
        name=doc.name,
        entry_node_id=node_ids[doc.entry],
        nodes=nodes,
        edges=edges,
        created_at=created_at,
        # A session declaration mints no id (issue #144): its authored name identifies
        # it, and dict insertion order is the only source of authored ordering.
        sessions=list(doc.sessions.values()),
    )


class GraphMintService:
    """Validate, reify, and persist a graph — the ``POST /graphs`` domain rule.

    Holds the *write* graph repository (``bzh:controller-read-only``), and raises
    :class:`GraphValidationError` so an invalid definition never persists."""

    def __init__(self, *, graphs: IWriteGraphRepository, clock: IClock) -> None:
        self._graphs = graphs
        self._clock = clock

    def mint(self, doc: GraphDoc, *, definition_yaml: str) -> tuple[Graph, list[str]]:
        result = Validator.of(doc).result
        if not result.ok:
            raise GraphValidationError(result)
        graph = reify_graph(doc, self._clock)
        warnings = [*result.warnings, *self._cross_graph_warnings(graph)]
        self._graphs.mint(graph, definition_yaml=definition_yaml, at=graph.created_at)
        return graph, warnings

    def _cross_graph_warnings(self, graph: Graph) -> list[str]:
        """Late-bound resolvability of cross-graph targets (issue #90) — a **warning**,
        never an error: a ``graph:<name>`` target resolves by name at apply time, so a
        target not minted yet is legal. The one mint-time step touching the repository,
        which keeps :class:`Validator` pure."""
        warnings: list[str] = []
        seen: set[str] = set()
        for edge in graph.edges:
            target = edge.target_graph
            if target is None or target in seen:
                continue
            seen.add(target)
            if self._graphs.get_enabled_by_name(target) is None:
                warnings.append(
                    f"cross-graph target `{target}` names no enabled graph yet — it will resolve "
                    f"when a graph named `{target}` is minted"
                )
        return warnings

    def mint_if_changed(self, doc: GraphDoc, *, definition_yaml: str, minted: GraphDoc | None) -> Graph | None:
        """Mint ``doc`` only if it differs from ``minted``, the store's newest of its name.

        Returns the freshly minted :class:`Graph`, ``None`` when already up to date, and
        raises as :meth:`mint` does — an invalid graph is never skipped as "unchanged".
        Comparing *parsed* docs, not source YAML, makes "only if changed" correct."""
        if minted is not None and minted == doc:
            return None
        graph, _ = self.mint(doc, definition_yaml=definition_yaml)
        return graph

    def ensure_default(self, doc: GraphDoc, *, definition_yaml: str) -> Graph:
        """Mint the configured default graph if no graph of its name has ever existed.

        Idempotent by name. A ``None`` from ``get_enabled_by_name`` is ambiguous, so
        :meth:`list_all` disambiguates (issue #101) — pinned by
        tests/test_graph_lifecycle_api.py::test_retiring_every_version_of_the_default_graph_survives_a_restart"""
        existing = self._graphs.get_enabled_by_name(doc.name)
        if existing is not None:
            return existing
        if any(g.name == doc.name for g in self._graphs.list_all()):
            raise DefaultGraphRetired(doc.name)
        graph, _ = self.mint(doc, definition_yaml=definition_yaml)
        return graph
