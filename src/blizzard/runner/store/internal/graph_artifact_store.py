"""SQLAlchemy adapter for the graph-artifact repository seam (package-private, blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.artifacts import GraphArtifactRecord, IWriteGraphArtifactRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import graph_artifacts

_log = get_logger("blizzard.runner.store")


class GraphArtifactStore:
    """Read-write graph-artifact adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def graph_artifacts_for_graph(self, graph_id: str) -> list[GraphArtifactRecord]:
        # Explicit order_by (`bzh:sql-portable`) — authored `artifacts:` position, not insert order.
        rows = self._store.all(
            select(graph_artifacts).where(graph_artifacts.c.graph_id == graph_id).order_by(graph_artifacts.c.ordinal)
        )
        return [
            GraphArtifactRecord(
                name=str(r.name), ordinal=int(r.ordinal), kind=ArtifactKind(str(r.kind)), content=str(r.content)
            )
            for r in rows
        ]

    def record_graph_artifacts(
        self, *, graph_id: str, artifacts: list[GraphArtifactRecord], recorded_at: datetime
    ) -> None:
        # A mint declaring nothing writes no row, so the presence check below would never
        # find one and every later lease off that mint would redo the check and re-log it.
        if not artifacts:
            return
        # Check-then-insert in one transaction (`bzh:sql-portable`) — an immutable mint's
        # declarations never change, so a second call for the same graph_id is a no-op.
        with self._store.begin() as conn:
            existing = conn.execute(
                select(graph_artifacts.c.graph_id).where(graph_artifacts.c.graph_id == graph_id)
            ).first()
            if existing is not None:
                return
            for artifact in artifacts:
                conn.execute(
                    graph_artifacts.insert().values(
                        graph_id=graph_id,
                        name=artifact.name,
                        ordinal=artifact.ordinal,
                        kind=artifact.kind.value,
                        content=artifact.content,
                        recorded_at=recorded_at,
                    )
                )
        _log.info("graph artifacts pinned", graph_id=graph_id, count=len(artifacts))


def _conforms_graph_artifact_store(x: GraphArtifactStore) -> IWriteGraphArtifactRepository:
    return x
