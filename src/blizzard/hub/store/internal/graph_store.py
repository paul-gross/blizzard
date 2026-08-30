"""SQLAlchemy adapter for the graph repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Graphs are
immutable: :meth:`mint` is insert-only, with no update path. ``enabled``/``retired`` is
not a column but is derived from the append-only ``graph_lifecycle_facts`` table,
newest-fact-wins per ``graph_id`` (issue #101)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, insert, select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import (
    Choice,
    ChoiceTarget,
    Edge,
    Graph,
    GraphArtifact,
    IWriteGraphRepository,
    Node,
    ProducesSpec,
    RotatePolicy,
    RunStep,
    SessionDecl,
)
from blizzard.hub.store.schema import (
    graph_artifacts,
    graph_choices,
    graph_edges,
    graph_lifecycle_facts,
    graph_nodes,
    graph_policy_facts,
    graph_sessions,
    graphs,
)


@dataclass(frozen=True)
class ListColumn[T]:
    """One JSON ``TEXT`` column holding a list — the shell each entry codec below fills in.

    ``None`` — a fresh column default — reads as the empty list."""

    def encode(self, items: list[T]) -> str:
        return json.dumps([self.entry(item) for item in items])

    def decode(self, value: str | None) -> list[T]:
        return [self.of(raw) for raw in json.loads(value)] if value else []

    def entry(self, item: T) -> Any:
        raise NotImplementedError

    def of(self, raw: Any) -> T:
        raise NotImplementedError


@dataclass(frozen=True)
class TextColumn(ListColumn[str]):
    """A ``list[str]`` column — a node's ``checks``, a session's ``model``."""

    def entry(self, item: str) -> Any:
        return item

    def of(self, raw: Any) -> str:
        return str(raw)


@dataclass(frozen=True)
class ProducesColumn(ListColumn[ProducesSpec]):
    """The ``graph_nodes.produces`` column (D1, issue #143).

    Two encodings share it: the ``{name, kind}`` mapping this writes, and a bare string
    normalized to an ``ASSET`` entry — read-side back-compat, so no migration is owed."""

    def entry(self, item: ProducesSpec) -> Any:
        return {"name": item.name, "kind": item.kind.value}

    def of(self, raw: Any) -> ProducesSpec:
        if isinstance(raw, str):
            return ProducesSpec(name=str(raw), kind=ArtifactKind.ASSET)
        return ProducesSpec(name=str(raw["name"]), kind=ArtifactKind(str(raw["kind"])))


@dataclass(frozen=True)
class RunColumn(ListColumn[RunStep]):
    """The ``graph_nodes.run`` column — a hub command node's steps (#65)."""

    def entry(self, item: RunStep) -> Any:
        return {"command": item.command, "name": item.name, "produces": item.produces}

    def of(self, raw: Any) -> RunStep:
        return RunStep(command=str(raw["command"]), name=raw.get("name"), produces=raw.get("produces"))


TEXTS = TextColumn()
PRODUCES = ProducesColumn()
RUN = RunColumn()


@dataclass(frozen=True)
class SessionRow:
    def values(self, decl: SessionDecl, *, graph_id: str, ordinal: int) -> dict[str, Any]:
        return {
            "graph_id": graph_id,
            "name": decl.name,
            "ordinal": ordinal,
            "model": TEXTS.encode(list(decl.model)),
            "effort": decl.effort,
            "rotate_max_context_tokens": decl.rotate.max_context_tokens if decl.rotate else None,
            "rotate_max_transcript_bytes": decl.rotate.max_transcript_bytes if decl.rotate else None,
            "rotate_max_invocations": decl.rotate.max_invocations if decl.rotate else None,
            "compaction_window": decl.compaction_window,
        }

    def of(self, row: Any) -> SessionDecl:
        """Three null ``rotate_*`` columns hydrate ``rotate=None``, not an all-null
        :class:`RotatePolicy`, so a round-tripped graph compares equal to the reified one."""
        bounds = (row.rotate_max_context_tokens, row.rotate_max_transcript_bytes, row.rotate_max_invocations)
        return SessionDecl(
            name=row.name,
            model=TEXTS.decode(row.model),
            effort=row.effort,
            rotate=RotatePolicy(*bounds) if any(b is not None for b in bounds) else None,
            compaction_window=row.compaction_window,
        )


@dataclass(frozen=True)
class GraphArtifactRow:
    def values(self, artifact: GraphArtifact, *, graph_id: str) -> dict[str, Any]:
        return {
            "graph_id": graph_id,
            "name": artifact.name,
            "ordinal": artifact.ordinal,
            "content": artifact.content,
        }

    def of(self, row: Any) -> GraphArtifact:
        return GraphArtifact(name=row.name, content=row.content, ordinal=row.ordinal)


@dataclass(frozen=True)
class ChoiceRow:
    def values(self, choice: Choice, *, node_id: str) -> dict[str, Any]:
        return {
            "choice_id": choice.choice_id,
            "node_id": node_id,
            "name": choice.name,
            "description": choice.description,
            "requires_checks": choice.requires_checks,
        }

    def of(self, row: Any) -> Choice:
        return Choice(
            choice_id=row.choice_id,
            name=row.name,
            description=row.description,
            requires_checks=bool(row.requires_checks),
        )


@dataclass(frozen=True)
class EdgeRow:
    def values(self, edge: Edge) -> dict[str, Any]:
        return {
            "edge_id": f"{edge.from_node_id}:{edge.choice_id}",
            "from_node_id": edge.from_node_id,
            "choice_id": edge.choice_id,
            "to_node_name": edge.to_node_name,
            "prompt_addendum": edge.prompt_addendum,
            "to_graph_model": edge.model,
        }

    def of(self, row: Any) -> Edge:
        return Edge(
            from_node_id=row.from_node_id,
            choice_id=row.choice_id,
            to_node_name=row.to_node_name,
            prompt_addendum=row.prompt_addendum,
            # The cross-graph target is re-derived from the raw ``to_node_name`` (#90),
            # not a stored column; the per-choice model override is its own column.
            target_graph=ChoiceTarget.of(row.to_node_name).graph,
            model=row.to_graph_model,
        )


@dataclass(frozen=True)
class NodeRow:
    """An empty ``run`` stores NULL rather than ``[]``, which reads back empty either way."""

    def values(self, node: Node, *, graph_id: str) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "graph_id": graph_id,
            "name": node.name,
            "executor": node.executor.value,
            "prompt": node.prompt,
            "judgement_prompt": node.judgement_prompt,
            "session": node.session.value,
            "session_source": node.session_source,
            "judged_by": node.judged_by.value,
            "retries_max": node.retries_max,
            "retries_exhausted": node.retries_exhausted,
            "mode": node.mode,
            "produces": PRODUCES.encode(node.produces),
            "checks": TEXTS.encode(list(node.checks)),
            "checks_cwd": node.checks_cwd,
            "checks_timeout": node.checks_timeout,
            "bounce_cap": node.bounce_cap,
            "run": RUN.encode(node.run) if node.run else None,
            "poll_interval_seconds": node.poll_interval_seconds,
            "poll_timeout_seconds": node.poll_timeout_seconds,
            "proposes_work_items": node.proposes_work_items,
        }

    def of(self, row: Any, *, choices: list[Choice]) -> Node:
        return Node(
            node_id=row.node_id,
            graph_id=row.graph_id,
            name=row.name,
            executor=Executor(row.executor),
            prompt=row.prompt,
            checks=TEXTS.decode(row.checks),
            checks_cwd=row.checks_cwd,
            checks_timeout=row.checks_timeout,
            produces=PRODUCES.decode(row.produces),
            session=SessionMode(row.session),
            session_source=row.session_source,
            judged_by=JudgedBy(row.judged_by),
            retries_max=row.retries_max,
            retries_exhausted=row.retries_exhausted,
            mode=row.mode,
            judgement_prompt=row.judgement_prompt,
            bounce_cap=row.bounce_cap,
            run=RUN.decode(row.run),
            poll_interval_seconds=row.poll_interval_seconds,
            poll_timeout_seconds=row.poll_timeout_seconds,
            proposes_work_items=bool(row.proposes_work_items),
            choices=choices,
        )


SESSIONS = SessionRow()
GRAPH_ARTIFACTS = GraphArtifactRow()
CHOICES = ChoiceRow()
EDGES = EdgeRow()
NODES = NodeRow()


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
            for ordinal, decl in enumerate(graph.sessions):
                values = SESSIONS.values(decl, graph_id=graph.graph_id, ordinal=ordinal)
                conn.execute(insert(graph_sessions).values(values))
            for artifact in graph.artifacts:
                conn.execute(insert(graph_artifacts).values(GRAPH_ARTIFACTS.values(artifact, graph_id=graph.graph_id)))
            for node in graph.nodes:
                conn.execute(insert(graph_nodes).values(NODES.values(node, graph_id=graph.graph_id)))
                for choice in node.choices:
                    conn.execute(insert(graph_choices).values(CHOICES.values(choice, node_id=node.node_id)))
            for edge in graph.edges:
                conn.execute(insert(graph_edges).values(EDGES.values(edge)))

    def get(self, graph_id: str) -> Graph | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(graphs).where(graphs.c.graph_id == graph_id)).one_or_none()
            if row is None:
                return None
            return self._reify(conn, row)

    def get_enabled_by_name(self, name: str) -> Graph | None:
        with self._engine.connect() as conn:
            # Tie-break on graph_id descending — ULIDs sort lexically by creation —
            # then walked newest-first, skipping every retired graph_id (issue #101).
            rows = conn.execute(
                select(graphs)
                .where(graphs.c.name == name)
                .order_by(graphs.c.created_at.desc(), graphs.c.graph_id.desc())
            ).all()
            for row in rows:
                if not self._is_retired(conn, row.graph_id):
                    return self._reify(conn, row)
            return None

    def newest_definition_yaml(self, name: str) -> str | None:
        # Same ordering as get_enabled_by_name, minus the retired filter — see the
        # protocol docstring for why reconciliation compares against the newest *minted*.
        with self._engine.connect() as conn:
            row = conn.execute(
                select(graphs.c.definition_yaml)
                .where(graphs.c.name == name)
                .order_by(graphs.c.created_at.desc(), graphs.c.graph_id.desc())
                .limit(1)
            ).first()
        return str(row.definition_yaml) if row is not None else None

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

    def follow_latest(self, graph_id: str) -> bool | None:
        """Newest ``graph_policy_facts`` row for ``graph_id`` wins; no row reads ``None``
        (inherit the hub setting) — issue #164.

        Ordered by ``id``, not ``set_at``: two facts inside one clock tick must still
        resolve newest-write-wins. A present ``None`` is a deliberate revert-to-inherit."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(graph_policy_facts.c.follow_latest)
                .where(graph_policy_facts.c.graph_id == graph_id)
                .order_by(graph_policy_facts.c.id.desc())
                .limit(1)
            ).first()
        if row is None or row.follow_latest is None:
            return None
        return bool(row.follow_latest)

    def record_policy(self, graph_id: str, *, follow_latest: bool | None, at: datetime, by: str) -> None:
        """Append a follow-latest policy fact — newest-fact-wins (issue #164)."""
        with self._engine.begin() as conn:
            conn.execute(
                insert(graph_policy_facts).values(graph_id=graph_id, follow_latest=follow_latest, set_at=at, set_by=by)
            )

    def _reify(self, conn, graph_row) -> Graph:  # type: ignore[no-untyped-def]
        node_rows = conn.execute(select(graph_nodes).where(graph_nodes.c.graph_id == graph_row.graph_id)).all()
        nodes: list[Node] = []
        for nr in node_rows:
            choice_rows = conn.execute(select(graph_choices).where(graph_choices.c.node_id == nr.node_id)).all()
            nodes.append(NODES.of(nr, choices=[CHOICES.of(c) for c in choice_rows]))
        node_ids = {n.node_id for n in nodes}
        edge_rows = conn.execute(select(graph_edges).where(graph_edges.c.from_node_id.in_(node_ids))).all()
        edges = [EDGES.of(er) for er in edge_rows]
        session_rows = conn.execute(
            select(graph_sessions)
            .where(graph_sessions.c.graph_id == graph_row.graph_id)
            .order_by(graph_sessions.c.ordinal)
        ).all()
        artifact_rows = conn.execute(
            select(graph_artifacts)
            .where(graph_artifacts.c.graph_id == graph_row.graph_id)
            .order_by(graph_artifacts.c.ordinal)
        ).all()
        return Graph(
            graph_id=graph_row.graph_id,
            name=graph_row.name,
            entry_node_id=graph_row.entry_node_id,
            nodes=nodes,
            edges=edges,
            created_at=graph_row.created_at,
            sessions=[SESSIONS.of(sr) for sr in session_rows],
            artifacts=[GRAPH_ARTIFACTS.of(ar) for ar in artifact_rows],
        )


def _conforms_graph_store(x: GraphStore) -> IWriteGraphRepository:
    return x
