"""Graph reification and the mint service.

A validated :class:`GraphDoc` is compiled into an immutable, id-carrying
:class:`Graph` (:class:`Reification`) and persisted (:class:`GraphMintService`); the
raw YAML is stored verbatim for audit and re-export. Validation errors reject the
mint (:class:`GraphValidationError`); warnings ride along on the minted graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import CHOICE_PREFIX, GRAPH_PREFIX, NODE_PREFIX, mint
from blizzard.hub.domain.graph import (
    RESERVED_TERMINAL,
    Choice,
    ChoiceDoc,
    Edge,
    Graph,
    GraphDoc,
    IWriteGraphRepository,
    JudgedBy,
    Node,
    NodeDoc,
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


@dataclass(frozen=True)
class Reification:
    """A validated authoring doc under compilation, with every id it needs already struck.

    :meth:`of` does the minting, so :attr:`graph` is pure and repeatable — one graph id, a
    node id per node, a choice id per (node, choice) position."""

    doc: GraphDoc
    graph_id: str
    node_ids: dict[str, str]
    choice_ids: dict[tuple[int, int], str]
    created_at: datetime

    @classmethod
    def of(cls, doc: GraphDoc, clock: IClock) -> Reification:
        return cls(
            doc=doc,
            graph_id=mint(GRAPH_PREFIX, clock),
            node_ids={node.name: mint(NODE_PREFIX, clock) for node in doc.nodes},
            created_at=clock.now(),
            choice_ids={
                (index, position): mint(CHOICE_PREFIX, clock)
                for index, nd in enumerate(doc.nodes)
                for position, _ in enumerate(cls._choice_docs(nd))
            },
        )

    @property
    def graph(self) -> Graph:
        """The compiled graph — fused choice/edge entries split into reified :class:`Choice`
        objects on the node and directed :class:`Edge` objects keyed by choice id."""
        return Graph(
            graph_id=self.graph_id,
            name=self.doc.name,
            entry_node_id=self.node_ids[self.doc.entry],
            nodes=[self._node(index, nd) for index, nd in enumerate(self.doc.nodes)],
            edges=[edge for index, nd in enumerate(self.doc.nodes) for edge in self._edges(index, nd)],
            created_at=self.created_at,
            # A session declaration mints no id (issue #144): its authored name identifies
            # it, and dict insertion order is the only source of authored ordering.
            sessions=list(self.doc.sessions.values()),
        )

    @staticmethod
    def _choice_docs(nd: NodeDoc) -> list[ChoiceDoc]:
        return list(nd.judgement.choices) if nd.judgement is not None else []

    def _node(self, index: int, nd: NodeDoc) -> Node:
        return Node(
            node_id=self.node_ids[nd.name],
            graph_id=self.graph_id,
            name=nd.name,
            executor=nd.executor,
            prompt=nd.prompt,
            checks=list(nd.checks),
            produces=list(nd.produces),
            session=nd.session,
            session_source=nd.session_source,
            judged_by=nd.judgement.by if nd.judgement is not None else JudgedBy.WORKER,
            retries_max=nd.retries_max,
            retries_exhausted=nd.retries_exhausted,
            mode=nd.mode,
            judgement_prompt=nd.judgement.prompt if nd.judgement is not None else None,
            choices=[
                Choice(
                    choice_id=self.choice_ids[index, position],
                    name=choice.name,
                    description=choice.description or "",
                    requires_checks=choice.requires_checks,
                )
                for position, choice in enumerate(self._choice_docs(nd))
            ],
            bounce_cap=nd.bounce_cap,
            run=[RunStep(command=r.command, name=r.name, produces=r.produces) for r in nd.run],
            poll_interval_seconds=nd.poll_interval_seconds,
            poll_timeout_seconds=nd.poll_timeout_seconds,
            checks_cwd=nd.checks_cwd,
            checks_timeout=nd.checks_timeout,
        )

    def _edges(self, index: int, nd: NodeDoc) -> list[Edge]:
        return [
            Edge(
                from_node_id=self.node_ids[nd.name],
                choice_id=self.choice_ids[index, position],
                to_node_name=choice.to or RESERVED_TERMINAL,
                prompt_addendum=choice.prompt_addendum,
                target_graph=choice.target_graph,
                model=choice.model,
            )
            for position, choice in enumerate(self._choice_docs(nd))
        ]


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
        graph = Reification.of(doc, self._clock).graph
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
