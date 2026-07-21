"""SQLAlchemy adapter for the graph repository seam (package-private).

Implements :class:`~blizzard.hub.domain.graph.IWriteGraphRepository` over the
``graphs`` / ``graph_nodes`` / ``graph_choices`` / ``graph_edges`` tables. All
``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the domain
sees only reified :class:`~blizzard.hub.domain.graph.Graph` objects.

Graphs are immutable: :meth:`mint` is insert-only, and there is no update
path. ``enabled``/``retired`` is not a column on ``graphs`` — it is derived from the
append-only ``graph_lifecycle_facts`` table (issue #101): ``get_enabled_by_name``
returns the newest **non-retired** graph of that name, newest-fact-wins per
``graph_id``, exactly like ``chunk_pause_facts``.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Engine, insert, select

from blizzard.hub.domain.graph import (
    Choice,
    Edge,
    Executor,
    Graph,
    IWriteGraphRepository,
    JudgedBy,
    Node,
    RunStep,
    SessionMode,
    target_graph_of,
)
from blizzard.hub.store.schema import graph_choices, graph_edges, graph_lifecycle_facts, graph_nodes, graphs


class GraphStore:
    """Read-write graph adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def mint(self, graph: Graph, *, definition_yaml: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(graphs).values(
                    graph_id=graph.graph_id,
                    name=graph.name,
                    entry_node_id=graph.entry_node_id,
                    definition_yaml=definition_yaml,
                    created_at=at,
                )
            )
            for node in graph.nodes:
                conn.execute(
                    insert(graph_nodes).values(
                        node_id=node.node_id,
                        graph_id=graph.graph_id,
                        name=node.name,
                        executor=node.executor.value,
                        prompt=node.prompt,
                        judgement_prompt=node.judgement_prompt,
                        session=node.session.value,
                        session_source=node.session_source,
                        judged_by=node.judged_by.value,
                        retries_max=node.retries_max,
                        retries_exhausted=node.retries_exhausted,
                        mode=node.mode,
                        produces=json.dumps(list(node.produces)),
                        checks=json.dumps(list(node.checks)),
                        bounce_cap=node.bounce_cap,
                        run=json.dumps([_run_step_to_json(r) for r in node.run]) if node.run else None,
                        poll_interval_seconds=node.poll_interval_seconds,
                        poll_timeout_seconds=node.poll_timeout_seconds,
                    )
                )
                for choice in node.choices:
                    conn.execute(
                        insert(graph_choices).values(
                            choice_id=choice.choice_id,
                            node_id=node.node_id,
                            name=choice.name,
                            description=choice.description,
                        )
                    )
            for edge in graph.edges:
                conn.execute(
                    insert(graph_edges).values(
                        edge_id=f"{edge.from_node_id}:{edge.choice_id}",
                        from_node_id=edge.from_node_id,
                        choice_id=edge.choice_id,
                        to_node_name=edge.to_node_name,
                        prompt_addendum=edge.prompt_addendum,
                        to_graph_model=edge.model,
                    )
                )

    def get(self, graph_id: str) -> Graph | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(graphs).where(graphs.c.graph_id == graph_id)).one_or_none()
            if row is None:
                return None
            return self._reify(conn, row)

    def get_enabled_by_name(self, name: str) -> Graph | None:
        with self._engine.connect() as conn:
            # Tie-break on graph_id descending (ULIDs sort lexically by creation) — kept
            # in lockstep with domain.graph.mark_effective's tie order. Walked
            # newest-first, skipping every retired graph_id (issue #101), so the
            # candidate is the newest **non-retired** graph of that name — or None once
            # every candidate has been skipped.
            rows = conn.execute(
                select(graphs)
                .where(graphs.c.name == name)
                .order_by(graphs.c.created_at.desc(), graphs.c.graph_id.desc())
            ).all()
            for row in rows:
                if not self._is_retired(conn, row.graph_id):
                    return self._reify(conn, row)
            return None

    def list_all(self) -> list[Graph]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(graphs).order_by(graphs.c.created_at.desc())).all()
            return [self._reify(conn, row) for row in rows]

    def is_retired(self, graph_id: str) -> bool:
        with self._engine.connect() as conn:
            return self._is_retired(conn, graph_id)

    def _is_retired(self, conn, graph_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Newest ``graph_lifecycle_facts`` row for ``graph_id`` wins; no row reads
        not-retired (a freshly minted graph starts enabled)."""
        row = conn.execute(
            select(graph_lifecycle_facts.c.retired)
            .where(graph_lifecycle_facts.c.graph_id == graph_id)
            .order_by(graph_lifecycle_facts.c.id.desc())
            .limit(1)
        ).first()
        return bool(row.retired) if row is not None else False

    def retired_graph_ids(self) -> set[str]:
        """Every ``graph_id`` whose newest lifecycle fact reads retired (issue #101)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(graph_lifecycle_facts.c.graph_id, graph_lifecycle_facts.c.retired).order_by(
                    graph_lifecycle_facts.c.id
                )
            ).all()
        newest: dict[str, bool] = {}
        for row in rows:
            newest[row.graph_id] = row.retired  # newest-fact-wins: ascending id order overwrites
        return {graph_id for graph_id, retired in newest.items() if retired}

    def record_lifecycle(self, graph_id: str, *, retired: bool, at: datetime, by: str) -> None:
        """Append a ``graph.retired``/``graph.enabled`` fact — newest-fact-wins (issue #101)."""
        with self._engine.begin() as conn:
            conn.execute(insert(graph_lifecycle_facts).values(graph_id=graph_id, retired=retired, set_at=at, set_by=by))

    def _reify(self, conn, graph_row) -> Graph:  # type: ignore[no-untyped-def]
        node_rows = conn.execute(select(graph_nodes).where(graph_nodes.c.graph_id == graph_row.graph_id)).all()
        nodes: list[Node] = []
        for nr in node_rows:
            choice_rows = conn.execute(select(graph_choices).where(graph_choices.c.node_id == nr.node_id)).all()
            nodes.append(
                Node(
                    node_id=nr.node_id,
                    graph_id=nr.graph_id,
                    name=nr.name,
                    executor=Executor(nr.executor),
                    prompt=nr.prompt,
                    checks=_json_list(nr.checks),
                    produces=_json_list(nr.produces),
                    session=SessionMode(nr.session),
                    session_source=nr.session_source,
                    judged_by=JudgedBy(nr.judged_by),
                    retries_max=nr.retries_max,
                    retries_exhausted=nr.retries_exhausted,
                    mode=nr.mode,
                    judgement_prompt=nr.judgement_prompt,
                    bounce_cap=nr.bounce_cap,
                    run=_run_steps(nr.run),
                    poll_interval_seconds=nr.poll_interval_seconds,
                    poll_timeout_seconds=nr.poll_timeout_seconds,
                    choices=[
                        Choice(choice_id=c.choice_id, name=c.name, description=c.description) for c in choice_rows
                    ],
                )
            )
        node_ids = {n.node_id for n in nodes}
        edge_rows = conn.execute(select(graph_edges).where(graph_edges.c.from_node_id.in_(node_ids))).all()
        edges = [
            Edge(
                from_node_id=er.from_node_id,
                choice_id=er.choice_id,
                to_node_name=er.to_node_name,
                prompt_addendum=er.prompt_addendum,
                # The cross-graph target is re-derived from the raw ``to_node_name`` (#90),
                # not a stored column; the per-choice model override is its own column.
                target_graph=target_graph_of(er.to_node_name),
                model=er.to_graph_model,
            )
            for er in edge_rows
        ]
        return Graph(
            graph_id=graph_row.graph_id,
            name=graph_row.name,
            entry_node_id=graph_row.entry_node_id,
            nodes=nodes,
            edges=edges,
            created_at=graph_row.created_at,
        )


def _run_step_to_json(step: RunStep) -> dict[str, str | None]:
    return {"command": step.command, "name": step.name, "produces": step.produces}


def _run_steps(value: str | None) -> list[RunStep]:
    """Decode a JSON-encoded ``list[{command, name, produces}]`` ``run`` column."""
    if not value:
        return []
    return [
        RunStep(command=str(r["command"]), name=r.get("name"), produces=r.get("produces")) for r in json.loads(value)
    ]


def _json_list(value: str | None) -> list[str]:
    """Decode a JSON-encoded ``list[str]`` node column (``produces``/``checks``).

    ``None`` (a row predating the graph-node-produces-checks revision, or a fresh
    column default) reads as the empty list — the same value the walking skeleton
    hardcoded before these were round-tripped."""
    return [str(x) for x in json.loads(value)] if value else []


def _conforms_graph_store(x: GraphStore) -> IWriteGraphRepository:
    return x
