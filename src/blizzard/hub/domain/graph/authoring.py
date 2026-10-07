"""Graph reification and the mint service.

A validated :class:`GraphDoc` is compiled into an immutable, id-carrying
:class:`Graph` (:class:`Reification`) and persisted (:class:`GraphMintService`); the
raw YAML is stored verbatim for audit and re-export. Validation errors reject the
mint (:class:`GraphValidationError`); warnings ride along on the minted graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.node_steps import JudgedBy
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.graph.model import (
    RESERVED_TERMINAL,
    Choice,
    ChoiceDoc,
    Edge,
    Graph,
    GraphArtifact,
    GraphDoc,
    IWriteGraphRepository,
    Node,
    NodeDoc,
    RunStep,
)
from blizzard.hub.domain.graph.validation import Validator, cross_graph_warnings


class DefaultGraphRetired(Exception):
    """Every minted graph named ``name`` is retired.

    Distinct from "never minted", which still mints; this refuses to silently re-mint
    over an operator's deliberate brake."""

    def __init__(self, name: str) -> None:
        super().__init__(f"every graph named {name!r} is retired — re-enable one or mint a new one before ingesting")
        self.name = name


def resolve_default(name: str, *, enabled: Graph | None, any_minted: bool) -> Graph | None:
    """Which graph work ingested with none lands on: ``enabled``, the newest enabled mint of the
    default name, when there is one; ``None`` — mint the default — when no graph of the name was
    ever minted; and :class:`DefaultGraphRetired` when every mint of it is retired, rather than
    re-minting over an operator's deliberate brake."""
    if enabled is not None:
        return enabled
    if any_minted:
        raise DefaultGraphRetired(name)
    return None


@domain_model
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
            graph_id=Id.mint(IdPrefix.GRAPH, clock).value,
            node_ids={node.name: Id.mint(IdPrefix.NODE, clock).value for node in doc.nodes},
            created_at=clock.now(),
            choice_ids={
                (index, position): Id.mint(IdPrefix.CHOICE, clock).value
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
            # A session declaration mints no id: its authored name identifies
            # it, and dict insertion order is the only source of authored ordering.
            sessions=list(self.doc.sessions.values()),
            # A graph artifact mints no id either — its authored name identifies it, and
            # its ordinal is struck from dict insertion order.
            artifacts=[
                GraphArtifact(name=name, content=content, ordinal=ordinal)
                for ordinal, (name, content) in enumerate(self.doc.artifacts.items())
            ],
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
            proposes_work_items=nd.proposes_work_items,
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

    Holds the *write* graph repository; the model refuses an invalid definition
    (:class:`~blizzard.hub.domain.graph.validation.GraphValidationError`) before anything persists."""

    def __init__(self, *, graphs: IWriteGraphRepository, clock: IClock) -> None:
        self._graphs = graphs
        self._clock = clock

    def mint(self, doc: GraphDoc, *, definition_yaml: str) -> tuple[Graph, list[str]]:
        graph, warnings = self._prepared(doc)
        self._graphs.mint(graph, definition_yaml=definition_yaml, at=graph.created_at)
        return graph, warnings

    def _prepared(self, doc: GraphDoc) -> tuple[Graph, list[str]]:
        """``doc`` validated and reified, with its warnings. The cross-graph reads resolve other
        graphs' names — a different aggregate — so they never run under a name lock."""
        result = Validator.of(doc).require_valid()
        graph = Reification.of(doc, self._clock).graph
        enabled = {t for t in graph.cross_graph_targets() if self._graphs.get_enabled_by_name(t) is not None}
        return graph, [*result.warnings, *cross_graph_warnings(graph, enabled_names=enabled)]

    def mint_if_changed(self, doc: GraphDoc, *, definition_yaml: str, minted: GraphDoc | None) -> Graph | None:
        """Mint ``doc`` only if it differs from ``minted``, the store's newest of its name.

        Returns the freshly minted :class:`Graph`, ``None`` when already up to date, and
        raises as :meth:`mint` does — an invalid graph is never skipped as "unchanged".
        Comparing *parsed* docs, not source YAML, makes "only if changed" correct."""
        if not doc.differs_from(minted):
            return None
        graph, _ = self.mint(doc, definition_yaml=definition_yaml)
        return graph

    def ensure_default_or_none(self, doc: GraphDoc, *, definition_yaml: str) -> Graph | None:
        """:meth:`ensure_default`, with retirement mapped to ``None`` rather than raised —
        for a caller that treats retirement as a transient condition to defer on (the
        materialization sweep's mint path), not one to surface as an error."""
        try:
            return self.ensure_default(doc, definition_yaml=definition_yaml)
        except DefaultGraphRetired:
            return None

    def ensure_default(self, doc: GraphDoc, *, definition_yaml: str) -> Graph:
        """Mint the configured default graph if no graph of its name has ever existed.

        Idempotent by name, and once under concurrency: the name is re-read under its lock
        (``bzh:store-exclusive-write``), so of two overlapping first calls one mints and the
        other returns that graph. A ``None`` from ``get_enabled_by_name`` is ambiguous, so
        :meth:`~blizzard.hub.domain.graph.model.IReadGraphRepository.any_minted` disambiguates
        — a cheap existence probe, not a full listing, to check membership
        by name — pinned by
        tests/test_graph_lifecycle_api.py::test_retiring_every_version_of_the_default_graph_survives_a_restart"""
        existing = self._graphs.get_enabled_by_name(doc.name)
        if existing is not None:
            return existing  # an enabled graph of the name stands; nothing to decide under the lock
        graph, _ = self._prepared(doc)
        with self._graphs.locked_name(doc.name) as handle:
            existing = handle.get_enabled_by_name(doc.name)
            any_minted = existing is None and handle.any_minted(doc.name)
            resolved = resolve_default(doc.name, enabled=existing, any_minted=any_minted)
            if resolved is not None:
                return resolved
            self._graphs.mint_locked(handle, graph, definition_yaml=definition_yaml, at=graph.created_at)
        return graph
